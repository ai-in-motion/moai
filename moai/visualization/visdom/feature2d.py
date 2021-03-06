from moai.visualization.visdom.base import Base
from moai.utils.color.colorize import get_colormap, COLORMAPS

import torch
import visdom
import functools
import typing
import logging

log = logging.getLogger(__name__)

__all__ = ["Feature2d"]

class Feature2d(Base):
    def __init__(self,
        keys:           typing.Union[str, typing.Sequence[str]],
        types:          typing.Union[str, typing.Sequence[str]],
        colormaps:      typing.Union[str, typing.Sequence[str]],
        transforms:     typing.Union[str, typing.Sequence[str]],
        name:           str="default",
        ip:             str="http://localhost",
        port:           int=8097,   
    ):
        super(Feature2d, self).__init__(name, ip, port)
        self.keys = [keys] if type(keys) is str else list(keys)
        self.types = [types] if type(types) is str else list(types)
        self.transforms = [transforms] if type(transforms) is str else list(transforms)
        self.colormaps = [colormaps] if type(colormaps) is str else list(colormaps)
        self.viz_map = {
            'color': functools.partial(self.__viz_color, self.visualizer),
            'heatmap': functools.partial(self.__viz_heatmap, self.visualizer),
        }
        self.transform_map = {
            'none': functools.partial(self.__no_transform),
            'minmax': functools.partial(self.__minmax_normalization)
        }
        self.colorize_map = { "none": lambda x: x }
        self.colorize_map.update(COLORMAPS)

    @property
    def name(self) -> str:
        return self.env_name
        
    def __call__(self, tensors: typing.Dict[str, torch.Tensor]) -> None:
        for k, t, tf, c in zip(self.keys, self.types, self.transforms, self.colormaps):
            if not k:
                continue
            tensor = tensors[k]
            b, h, _, __ = tensor.size()            
            for i in range(h):
                key = k + "_{}".format(i)
                self.viz_map[t](
                    self.colorize_map[c](
                        self.transform_map[tf](
                            tensor[:, i, ...].unsqueeze(1)
                        )), key, key, self.name
                )

    @staticmethod
    def __viz_color(
        visdom: visdom.Visdom,
        tensor: torch.Tensor,
        key: str,
        win: str,
        env: str
    ) -> None:
        visdom.images(
            tensor,
            win=win,
            env=env,
            opts={
                'title': key,
                'caption': key,
                'jpgquality': 50,
            }
        )

    @staticmethod
    def __viz_heatmap(
        visdom: visdom.Visdom,
        tensor: torch.Tensor,
        key: str,
        win: str,
        env: str
    ) -> None:
        b, _, __, ___ = tensor.size() # assumes [B, C, H, W], i.e. 2d train
        heatmaps = torch.flip(tensor, dims=[2]).detach().cpu()
        for i in range(b):
            opts = (
            {
                'title': key + "_{}".format(i),
                'colormap': 'Viridis'
            })
            visdom.heatmap(heatmaps[i, :, :, :].squeeze(), opts=opts, win=win + str(i))

    @staticmethod #TODO: refactor these into a common module
    def __no_transform(tensor: torch.Tensor) -> torch.Tensor:
        return tensor

    @staticmethod #TODO: refactor these into a common module
    def __minmax_normalization(tensor: torch.Tensor) -> torch.Tensor:
        b, c, __, ___ = tensor.size()
        min_v = torch.min(tensor.view(b, c, -1), dim=2, keepdim=True)[0].unsqueeze(3)
        max_v = torch.max(tensor.view(b, c, -1), dim=2, keepdim=True)[0].unsqueeze(3)
        return (tensor - min_v) / (max_v - min_v)
    


