"""Get configurable parameters."""

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

# TODO: This would require a new design.
# TODO: https://jira.devtools.intel.com/browse/IAAALD-149

from pathlib import Path
from typing import List, Optional, Union
from warnings import warn

import torch
from omegaconf import DictConfig, ListConfig, OmegaConf


def update_input_size_config(config: Union[DictConfig, ListConfig]) -> Union[DictConfig, ListConfig]:
    """Update config with image size as tuple, effective input size and tiling stride.

    Convert integer image size parameters into tuples, calculate the effective input size based on image size
    and crop size, and set tiling stride if undefined.

    Args:
        config (Union[DictConfig, ListConfig]): Configurable parameters object

    Returns:
        Union[DictConfig, ListConfig]: Configurable parameters with updated values
    """
    # handle image size
    if isinstance(config.dataset.image_size, int):
        config.dataset.image_size = (config.dataset.image_size,) * 2

    config.model.input_size = config.dataset.image_size

    if "tiling" in config.dataset.keys() and config.dataset.tiling.apply:
        if isinstance(config.dataset.tiling.tile_size, int):
            config.dataset.tiling.tile_size = (config.dataset.tiling.tile_size,) * 2
        if config.dataset.tiling.stride is None:
            config.dataset.tiling.stride = config.dataset.tiling.tile_size

    return config


def update_nncf_config(config: Union[DictConfig, ListConfig]) -> Union[DictConfig, ListConfig]:
    """Set the NNCF input size based on the value of the crop_size parameter in the configurable parameters object.

    Args:
        config (Union[DictConfig, ListConfig]): Configurable parameters of the current run.

    Returns:
        Union[DictConfig, ListConfig]: Updated configurable parameters in DictConfig object.
    """
    crop_size = config.dataset.image_size
    sample_size = (crop_size, crop_size) if isinstance(crop_size, int) else crop_size
    if (
        "optimization" in config.keys()
        and "nncf" in config.optimization.keys()
    ):
        config.optimization.nncf.input_info.sample_size = [1, 3, *sample_size]
        if (
            config.optimization.nncf.apply
            and "update_config" in config.optimization.nncf
        ):
            return OmegaConf.merge(config, config.optimization.nncf.update_config)
    return config


def update_multi_gpu_training_config(config: Union[DictConfig, ListConfig]) -> Union[DictConfig, ListConfig]:
    """Updates the config to change learning rate based on number of gpus assigned.

    Current behaviour is to ensure only ddp accelerator is used.

    Args:
        config (Union[DictConfig, ListConfig]): Configurable parameters for the current run

    Raises:
        ValueError: If unsupported accelerator is passed

    Returns:
        Union[DictConfig, ListConfig]: Updated config
    """
    # validate accelerator
    if (
        config.trainer.accelerator is not None
        and config.trainer.accelerator.lower() != "ddp"
    ):
        if config.trainer.accelerator.lower() not in (
            "dp",
            "ddp_spawn",
            "ddp2",
        ):
            raise ValueError(
                f"Unsupported accelerator found: {config.trainer.accelerator}. Should be one of [null, ddp]"
            )
        warn(
            f"Using accelerator {config.trainer.accelerator.lower()} is discouraged. "
            f"Please use one of [null, ddp]. Setting accelerator to ddp"
        )
        config.trainer.accelerator = "ddp"
    # Increase learning rate
    # since pytorch averages the gradient over devices, the idea is to
    # increase the learning rate by the number of devices
    if "lr" in config.model:
        # Number of GPUs can either be passed as gpus: 2 or gpus: [0,1]
        n_gpus: Union[int, List] = 1
        if "trainer" in config and "gpus" in config.trainer:
            n_gpus = config.trainer.gpus
        lr_scaler = n_gpus if isinstance(n_gpus, int) else len(n_gpus)
        config.model.lr = config.model.lr * lr_scaler
    return config


def update_device_config(config: Union[DictConfig, ListConfig], openvino: bool) -> Union[DictConfig, ListConfig]:
    """Update XPU Device Config This function ensures devices are configured correctly by the user.

    Args:
        config (Union[DictConfig, ListConfig]): Input config
        openvino (bool): Boolean to check if OpenVINO Inference is enabled.

    Returns:
        Union[DictConfig, ListConfig]: Updated config
    """

    config.openvino = openvino
    if openvino:
        config.trainer.gpus = 0

    if not torch.cuda.is_available():
        config.trainer.gpus = 0

    if config.trainer.gpus == 0 and torch.cuda.is_available():
        config.trainer.gpus = 1

    config = update_multi_gpu_training_config(config)

    return config


def get_configurable_parameters(
    model_name: Optional[str] = None,
    model_config_path: Optional[Union[Path, str]] = None,
    weight_file: Optional[str] = None,
    openvino: bool = False,
    config_filename: Optional[str] = "config",
    config_file_extension: Optional[str] = "yaml",
) -> Union[DictConfig, ListConfig]:
    """Get configurable parameters.

    Args:
        model_name: Optional[str]:  (Default value = None)
        model_config_path: Optional[Union[Path, str]]:  (Default value = None)
        weight_file: Path to the weight file
        openvino: Use OpenVINO
        config_filename: Optional[str]:  (Default value = "config")
        config_file_extension: Optional[str]:  (Default value = "yaml")

    Returns:
        Union[DictConfig, ListConfig]: Configurable parameters in DictConfig object.
    """
    if model_name is None and model_config_path is None:
        raise ValueError(
            "Both model_name and model config path cannot be None! "
            "Please provide a model name or path to a config file!"
        )

    if model_config_path is None:
        model_config_path = Path(f"anomalib/models/{model_name}/{config_filename}.{config_file_extension}")

    config = OmegaConf.load(model_config_path)

    # Dataset Configs
    if "format" not in config.dataset.keys():
        config.dataset.format = "mvtec"

    config = update_input_size_config(config)

    # Project Configs
    project_path = Path(config.project.path) / config.model.name / config.dataset.name / config.dataset.category
    (project_path / "weights").mkdir(parents=True, exist_ok=True)
    (project_path / "images").mkdir(parents=True, exist_ok=True)
    config.project.path = str(project_path)
    # loggers should write to results/model/dataset/category/ folder
    config.trainer.default_root_dir = str(project_path)

    if weight_file:
        config.model.weight_file = weight_file

    config = update_nncf_config(config)
    config = update_device_config(config, openvino)

    # thresholding
    if "pixel_default" not in config.model.threshold.keys():
        config.model.threshold.pixel_default = config.model.threshold.image_default

    return config
