from moai import __version__ as miV
from moai.utils.engine import NoOp as NoInterval
from moai.parameters.optimization import NoOp as NoOptimization
from moai.parameters.optimization.schedule import NoOp as NoScheduling
from moai.parameters.initialization import Default as NoInit
from moai.validation import NoOp as NoValidation
from moai.supervision import NoOp as NoSupervision

import torch
import numpy
import pytorch_lightning
import torch.utils.data as ptd
import omegaconf.omegaconf 
import hydra.utils as hyu
import typing
import types
import toolz
import logging

log = logging.getLogger(__name__)

__all__ = ['FeedForward']

def _create_supervision_block(
    cfg: omegaconf.DictConfig,
    force: bool=True
):
    if force and not cfg:
        log.warning("Empty supervision block in feedforward model.")
    return hyu.instantiate(cfg) if cfg else NoSupervision()

def _create_validation_block(
    cfg: omegaconf.DictConfig,
    force: bool=True
):
    if force and not cfg:
        log.warning("Empty validation block in feedforward model.")
    return hyu.instantiate(cfg) if cfg else NoValidation()

def _create_processing_block(
    cfg: omegaconf.DictConfig, 
    attribute: str, 
    monads: omegaconf.DictConfig,
    force: bool=False
):
    if force and not cfg and attribute in cfg:
        log.warning(f"Empty processing block ({attribute}) in feedforward model.")
    return hyu.instantiate(getattr(cfg, attribute), monads)\
        if cfg and attribute in cfg else torch.nn.Identity()

def _create_interval_block(
    cfg: omegaconf.DictConfig,
    force: bool=False
):
    if force and not cfg:
        log.warning("Empty interval block in feedforward model.")
    return hyu.instantiate(cfg) if cfg else NoInterval()

def _create_optimization_block(
    cfg: omegaconf.DictConfig,
    params: typing.Union[typing.Iterable[torch.Tensor], typing.Dict[str, torch.Tensor]],
    force: bool=False
):
    if force and not cfg:
        log.warning("No optimizer in feedforward model.")
    return hyu.instantiate(cfg, params) if cfg else NoOptimization(params)

def _create_scheduling_block(
    cfg: omegaconf.DictConfig, 
    optimizers: typing.Sequence[torch.optim.Optimizer],
    force: bool=False
):
    if force and not cfg:
        log.warning("No scheduling used in feedforward model.")
    return hyu.instantiate(cfg, optimizers) if cfg else NoScheduling(optimizers)

class FeedForward(pytorch_lightning.LightningModule):
    def __init__(self, 
        data:               omegaconf.DictConfig=None,
        parameters:         omegaconf.DictConfig=None,
        feedforward:        omegaconf.DictConfig=None,
        monads:             omegaconf.DictConfig=None,        
        supervision:        omegaconf.DictConfig=None,
        validation:         omegaconf.DictConfig=None,        
        visualization:      omegaconf.DictConfig=None,
        export:             omegaconf.DictConfig=None,
        hyperparameters:    typing.Union[omegaconf.DictConfig, typing.Mapping[str, typing.Any]]=None,
    ):
        super(FeedForward, self).__init__()        
        self.data = data
        self.initializer = parameters.initialization if parameters is not None else None
        self.optimization_config = parameters.optimization if parameters is not None else None
        self.schedule_config = parameters.schedule if parameters is not None else None
        self.supervision = _create_supervision_block(supervision)
        self.validation = _create_validation_block(validation) #TODO: change this, "empty processing block" is confusing
        self.preprocess = _create_processing_block(feedforward, "preprocess", monads=monads)
        self.postprocess = _create_processing_block(feedforward, "postprocess", monads=monads)
        self.visualizer = _create_interval_block(visualization)
        self.exporter = _create_interval_block(export)        
        #NOTE: __NEEDED__ for loading checkpoint
        hparams = hyperparameters if hyperparameters is not None else { }
        hparams.update({'moai_version': miV})
        self.hparams =  hparams
        self.global_test_step = 0

    def initialize_parameters(self) -> None:
        init = hyu.instantiate(self.initializer) if self.initializer else NoInit()
        init(self)

    def forward(self,
        tensors:                typing.Dict[str, torch.Tensor]
    ) -> typing.Dict[str, torch.Tensor]:
        pass

    def training_step(self, 
        batch:                  typing.Dict[str, torch.Tensor],
        batch_idx:              int,
        optimizer_idx:          int=None,
    ) -> typing.Dict[str, typing.Union[torch.Tensor, typing.Dict[str, torch.Tensor]]]:
        preprocessed = self.preprocess(batch)
        prediction = self(preprocessed)
        postprocessed = self.postprocess(prediction)
        total_loss, losses = self.supervision(postprocessed)
        #TODO: should add loss maps as return type to be able to forward them for visualization
        losses = toolz.keymap(lambda k: f"train_{k}", losses)
        losses.update({'total_loss': total_loss})        
        self.log_dict(losses, prog_bar=False, logger=True)        
        return { 'loss': total_loss, 'tensors': postprocessed }

    def training_step_end(self, 
        train_outputs: typing.Dict[str, typing.Union[torch.Tensor, typing.Dict[str, torch.Tensor]]]
    ) -> None:
        if self.global_step and (self.global_step % self.visualizer.interval == 0):
            self.visualizer(train_outputs['tensors'])
        if self.global_step and (self.global_step % self.exporter.interval == 0):
            self.exporter(train_outputs['tensors'])
        return train_outputs['loss']

    def validation_step(self,
        batch: typing.Dict[str, torch.Tensor],
        batch_nb: int
    ) -> dict:
        preprocessed = self.preprocess(batch)
        prediction = self(preprocessed)
        outputs = self.postprocess(prediction)
        #TODO: consider adding loss maps in the tensor dict
        metrics = self.validation(outputs)
        return metrics

    def validation_epoch_end(self,
        outputs: typing.List[dict]
    ) -> None:
        keys = next(iter(outputs), { }).keys()
        metrics = { }
        for key in keys:
            metrics[key] = numpy.mean(numpy.array(
                [d[key].item() for d in outputs]
            ))        
        self.log_dict(metrics, prog_bar=True, logger=False, on_epoch=True, sync_dist=True)
        log_metrics = toolz.keymap(lambda k: f"val_{k}", metrics)
        self.log_dict(log_metrics, prog_bar=False, logger=True, on_epoch=True, sync_dist=True)
    
    def test_step(self, 
        batch: typing.Dict[str, torch.Tensor],
        batch_nb: int
    ) -> dict:
        preprocessed = self.preprocess(batch)
        prediction = self(preprocessed)
        outputs = self.postprocess(prediction)
        metrics = self.validation(outputs)
        self.global_test_step += 1
        log_metrics = toolz.keymap(lambda k: f"test_{k}", metrics)
        self.log_dict(log_metrics, prog_bar=False, logger=True, on_step=True, on_epoch=False, sync_dist=True)
        return metrics, outputs

    def test_step_end(self,
        metrics_tensors: typing.Tuple[typing.Dict[str, torch.Tensor], typing.Dict[str, torch.Tensor]],        
    ) -> None:
        metrics, tensors = metrics_tensors
        if self.global_test_step and (self.global_test_step % self.exporter.interval == 0):
            self.exporter(tensors)
        if self.global_test_step and (self.global_test_step % self.visualizer.interval == 0):
            self.visualizer(tensors)
        return metrics

    def test_epoch_end(self, 
        outputs: typing.List[dict]
    ) -> dict:
        keys = next(iter(outputs), { }).keys()
        metrics = { }
        for key in keys:
            metrics[key] = numpy.mean(numpy.array(
                [d[key].item() for d in outputs]
            ))   
        self.log_dict(metrics, prog_bar=False, logger=True, on_epoch=True, sync_dist=True)

    def configure_optimizers(self) -> typing.Tuple[typing.List[torch.optim.Optimizer], typing.List[torch.optim.lr_scheduler._LRScheduler]]:
        log.info(f"Configuring optimizer and scheduler")
        self.optimization = _create_optimization_block(self.optimization_config, self.parameters())
        self.schedule = _create_scheduling_block(self.schedule_config, self.optimization.optimizers)
        return self.optimization.optimizers, self.schedule.schedulers

    def train_dataloader(self) -> ptd.DataLoader:        
        log.info(f"Instantiating ({self.data.train.iterator._target_.split('.')[-1]}) train set data iterator")
        train_iterator = hyu.instantiate(self.data.train.iterator)
        train_loader = hyu.instantiate(self.data.train.loader, train_iterator)
        return train_loader

    def val_dataloader(self) -> ptd.DataLoader:        
        log.info(f"Instantiating ({self.data.val.iterator._target_.split('.')[-1]}) validation set data iterator")
        val_iterator = hyu.instantiate(self.data.val.iterator)
        validation_loader = hyu.instantiate(self.data.val.loader, val_iterator)
        return validation_loader

    def test_dataloader(self) -> ptd.DataLoader:        
        log.info(f"Instantiating ({self.data.test.iterator._target_.split('.')[-1]}) test set data iterator")
        test_iterator = hyu.instantiate(self.data.test.iterator)
        test_loader = hyu.instantiate(self.data.test.loader, test_iterator)        
        return test_loader
