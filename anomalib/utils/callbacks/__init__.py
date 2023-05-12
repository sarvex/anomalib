"""Callbacks for Anomalib models."""

# Copyright (C) 2020 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions
# and limitations under the License.

import os
from importlib import import_module
from typing import List, Union

from omegaconf import DictConfig, ListConfig
from pytorch_lightning.callbacks import Callback, ModelCheckpoint

from .cdf_normalization import CdfNormalizationCallback
from .compress import CompressModelCallback
from .min_max_normalization import MinMaxNormalizationCallback
from .model_loader import LoadModelCallback
from .save_to_csv import SaveToCSVCallback
from .timer import TimerCallback
from .visualizer_callback import VisualizerCallback

__all__ = [
    "CompressModelCallback",
    "LoadModelCallback",
    "TimerCallback",
    "VisualizerCallback",
    "SaveToCSVCallback",
]


def get_callbacks(config: Union[ListConfig, DictConfig]) -> List[Callback]:
    """Return base callbacks for all the lightning models.

    Args:
        config (DictConfig): Model config

    Return:
        (List[Callback]): List of callbacks.
    """
    callbacks: List[Callback] = []

    monitor_metric = None if "early_stopping" not in config.model.keys() else config.model.early_stopping.metric
    monitor_mode = "max" if "early_stopping" not in config.model.keys() else config.model.early_stopping.mode

    checkpoint = ModelCheckpoint(
        dirpath=os.path.join(config.project.path, "weights"),
        filename="model",
        monitor=monitor_metric,
        mode=monitor_mode,
        auto_insert_metric_name=False,
    )

    callbacks.extend([checkpoint, TimerCallback()])

    if "weight_file" in config.model.keys():
        load_model = LoadModelCallback(os.path.join(config.project.path, config.model.weight_file))
        callbacks.append(load_model)

    if (
        "normalization_method" in config.model.keys()
        and config.model.normalization_method != "none"
    ):
        if config.model.normalization_method == "cdf":
            if config.model.name not in ["padim", "stfpm"]:
                raise NotImplementedError("Score Normalization is currently supported for PADIM and STFPM only.")
            if not config.optimization.nncf.apply:
                callbacks.append(CdfNormalizationCallback())
            else:
                raise NotImplementedError("CDF Score Normalization is currently not compatible with NNCF.")
        elif config.model.normalization_method == "min_max":
            callbacks.append(MinMaxNormalizationCallback())
        else:
            raise ValueError(f"Normalization method not recognized: {config.model.normalization_method}")

    if config.project.log_images_to != []:
        callbacks.append(
            VisualizerCallback(
                inputs_are_normalized=config.model.normalization_method
                != "none"
            )
        )

    if "optimization" in config.keys():
        if config.optimization.nncf.apply:
            # NNCF wraps torch's jit which conflicts with kornia's jit calls.
            # Hence, nncf is imported only when required
            nncf_module = import_module("anomalib.utils.callbacks.nncf_callback")
            nncf_callback = getattr(nncf_module, "NNCFCallback")
            callbacks.append(
                nncf_callback(
                    config=config,
                    dirpath=os.path.join(config.project.path, "compressed"),
                    filename="compressed_model",
                )
            )
        if config.optimization.compression.apply:
            callbacks.append(
                CompressModelCallback(
                    input_size=config.model.input_size,
                    dirpath=os.path.join(config.project.path, "compressed"),
                    filename="compressed_model",
                )
            )

    if "save_to_csv" in config.project.keys() and config.project.save_to_csv:
        callbacks.append(SaveToCSVCallback())

    return callbacks
