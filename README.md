# padertorch
[![Build Status](https://dev.azure.com/fgnt/fgnt/_apis/build/status/fgnt.padertorch?branchName=master)](https://dev.azure.com/fgnt/fgnt/_build/latest?definitionId=3&branchName=master)
[![Azure DevOps tests](https://img.shields.io/azure-devops/tests/fgnt/fgnt/3/master)](https://dev.azure.com/fgnt/fgnt/_build/latest?definitionId=3&branchName=master)
[![Azure DevOps coverage](https://img.shields.io/azure-devops/coverage/fgnt/fgnt/3/master)](https://dev.azure.com/fgnt/fgnt/_build/latest?definitionId=3&branchName=master)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/fgnt/lazy_dataset/blob/master/LICENSE)

Padertorch is designed to simplify the training of deep learning models written with [PyTorch](https://pytorch.org).
While focusing on speech and audio processing, it is not limited to these application areas.

This repository is currently under construction.

[//]: <> (The examples in contrib/examples are only working in the Paderborn NT environment)

# Highlights

- **Fast prototyping**: The trainer and models are all compatible with [sacred](https://github.com/IDSIA/sacred) and easily allow hyperparameter changes over the command line. Check out the [configurable](padertorch/configurable.py) module and the [examples](padertorch/contrib/examples) for how it works.
- **Easily extensible**: Write your own network modules and models based on `padertorch.Module` and `padertorch.Model`.
- **Seamless integration**: You provide your own data and model. We provide the trainer to wrap around your model and data. Calling `train` starts the training.
- **Forget the training loop**: Our `padertorch.Trainer` takes care of the repetitive training loop and allows you to focus on network tuning. Included are features such as:
  - Periodically executed validation runs
  - Automatic checkpointing: The parameters of the model and the state of the trainer are periodically saved. The checkpoint interval and number of total checkpoints are customizable: Keep one, some or all checkpoints. We also keep track of the best checkpoint on the validation data given some metric.
  - Resume from the latest checkpoint if the training was interrupted.
  - Learning rate scheduling
  - Backoff: Restore the best checkpoint and change the learning rate if the loss is not decreasing.
  - [Averaging multiple checkpoints]():
  - [Hooks](padertorch/train/hooks.py): Extend the basic features of the trainer with your own functionality.
- **Logging**: As logging backend, we use [tensorboardX](https://github.com/lanpa/tensorboardX) to generate a `tfevents` file that can be visualized from a [tensorboard](https://github.com/tensorflow/tensorboard). Custom values to be logged can be defined in subclasses of `padertorch.Model`.
- **Test run**: The trainer has a `test_run` function to train the model for few iterations and test if
    - the model is executable (burn test),
    - the validation is deterministic/reproducible, and
    - the model changes the parameter during training.
- **Virtual minibatch**:
  - The `Trainer` usually does not know if the model is trained with a single example or multiple examples (minibatch), because the examples that are yielded from the dataset are directly forwarded to the model.
  - When the `virtual_minibatch_size` option is larger than one, the trainer calls the forward and backward step `virtual_minibatch_size` times before applying the gradients. This increases the number of examples over which the gradient is calculated while the memory consumption stays similar. See [here](doc/virtual_batch_size_multi_gpu.md) for a more thorough explanation.
- **Multi-GPU training**: Easily deploy your model onto multiple GPUs to increase the total batch size and speed up the training. See [here](doc/virtual_batch_size_multi_gpu.md#L68) for implementation details and the [example](padertorch/contrib/examples/multi_gpu) for how to enable it.
- **Support for lazy data preparation**: Ever tired of pre-computing features, taking up time and space on the hard disk? Padertorch works with lazy dataloaders (e.g., [lazy_dataset](https://github.com/fgnt/lazy_dataset)) which extract the features on the fly!

# Features for Application in Deep Learning

Padertorch provides a selection of frequently used network architectures and functionalities such as activation and normalization, ready for you to integrate into your own models.

- [Multi-layer Feed-Forward](padertorch/modules/fully_connected.py): Multiple fully-connected layers with non-linearity and dropout.
- [CNN](padertorch/contrib/je/modules/conv.py) (currently subject to breaking changes and hardly any documentation): 1d- and 2d-CNNs with residual connections, dropout, gated activations, batch and sequence norm and correct handling of down- and upsampling.
- [Normalization](padertorch/modules/normalization.py): Perform normalization of arbitrary axes/dimensions of your features, keep track of running statistics and apply learnable affine transformation.
- [Collection of activation functions](padertorch/ops/mappings.py): Fast access of various activation functions with just a string.
- [Losses](padertorch/ops/losses): We provide an implementation of the [Kullback-Leibler divergence](padertorch/ops/losses/kl_divergence.py) and different [regression](padertorch/ops/losses/regression.py) objectives.

## Advanced Architectures

- [Dual-Path RNN (DPRNN)](padertorch/modules/dual_path_rnn.py): See the [paper](https://arxiv.org/abs/1910.06379).

## Support for sequential and speech data

Padertorch especially offers support for training with [sequential data](padertorch/ops/sequence) such as:
- [Masking](padertorch/ops/sequence/mask.py): Calculate a mask which has non-zero entries for non-padded positions and zero entries for padded positions in the sequence.
- [Fragmenting](padertorch/data/fragmenter.py): Fragment a sequence into smaller chunks of the same length.
- [Visualization in tensorboard](padertorch/summary/tbx_utils.py): Prepare spectrograms and speech masks for visualization and audio for playback in tensorboard.
- [Source separation objectives](padertorch/ops/losses/source_separation.py): Commonly used objectives for speech source separation such as PIT or deep clustering.

# Installation

**Requirements**
- Python 3
- torch >= 1.0

```bash
$ git clone https://github.com/fgnt/padertorch.git
$ cd padertorch && pip install -e .
```

# Getting Started

## A Short Explanation of `padertorch.Module` and `padertorch.Model`

You can build your models upon `padertorch.Module` and `padertorch.Model`.
Both expect a `forward` method which has the same functionality as the `forward` call of  `torch.nn.Module`: It takes data as input, applies some transformations, and returns the network output:

```python
def forward(self, example):
    x = example['x']
    out = transform(x)
    return out
```

Additionally, `padertorch.Model` expects a `review` method to be implemented which takes the data and output of the `forward` call as inputs from which it computes the training loss and metrics for logging in tensorboard:

```python
import torch

def review(self, example, output):
    # loss computation
    ce_loss = torch.nn.CrossEntropyLoss()(output, example['label'])
    # compute additional metrics
    with torch.no_grad():
        prediction = torch.argmax(output, dim=1)
        accuracy = (prediction == example['label']).float().mean()
    return {
        'loss': ce_loss,
        'scalars': {'accuracy': accuracy}
    }
```

See [padertorch.summary.tbx_utils.review_dict](padertorch/summary/tbx_utils.py#L213) for how the review dictionary should be constructed.
For each training step, the trainer calls `forward`, passes its output to `review` and performs a backpropagation step on the loss.
Typically, the input to the `forward` of a `Module` is a Tensor, while for a `Model`, it is a dictionary which contains additional entries, e.g., labels, which are needed in the `review`.
This is only a recommendation and there is no restriction for the input type.

While these two methods are mandatory, you are free to add any further methods to your models.
Since a `Module` does not need a `review` method, it can be used as a component of a `Model`.

## How to Integrate your Data and Model with the Trainer

The trainer works with any kind of iterable, e.g., `list`, `torch.utils.data.DataLoader` or `lazy_dataset.Dataset`.
The `train` method expects an iterable as input which yields training examples or minibatches of examples that are forwarded to the model without being interpreted by the trainer, i.e., the yielded entries can have any data type and only the model has to be designed to work with them.
In our [examples](padertorch/contrib/examples), the iterables always yield a `dict`.

The `Model` implements an `example_to_device` which is called by the trainer to move the data to a CPU or GPU.
Per default, `example_to_device` uses `padertorch.data.example_to_device` which recursively converts numpy arrays to Tensors and moves all Tensors to the available device.
The training device can be directly provided to the call of `Trainer.train`.
Otherwise, it is automatically set by the trainer according to `torch.cuda.is_available`.

Optionally, you can add an iterable with validation examples by using `Trainer.register_validation_hook`.
Some functionalities (e.g., keeping track of the best checkpoint) are then performed on the validation data.

A simple sketch for the trainer setup is given below:

```python
import torch
import padertorch as pt

train_dataset = ...
validation_dataset = ...

class MyModel(pt.Model):
    def __init__(self):
        self.net = torch.nn.Sequential(...)

    def forward(self, example):
        output = self.net(example['observation'])
        return output

    def review(self, example, output):
        loss = ...  # calculate loss
        with torch.no_grad():
            ...  # calculate general metrics
            if self.training:
                ...  # calculate training specific metrics
            else:
                ...  # calculate validation specific metrics
        return {
            'loss': loss,
            'scalars': {
                'accuracy': ...,
                ...
            },
        }  # Furthers keys: 'images', 'audios', 'histograms', 'texts', 'figures'


trainer = padertorch.Trainer(
    model=MyModel(),
    storage_dir=pt.io.get_new_storage_dir('my_experiment'),  # checkpoints of the trained model are stored here
    optimizer=pt.train.optimizer.Adam(),
    loss_weights=None,
    summary_trigger=(1, 'epoch'),
    checkpoint_trigger=(1, 'epoch'),
    stop_trigger=(1, 'epoch'),
    virtual_minibatch_size=1,
)
trainer.test_run(train_dataset, validation_dataset)
trainer.register_validation_hook(validation_dataset)
trainer.train(train_dataset)
```

See the [trainer](padertorch/train/trainer.py#L40) for an explanation of its signature.
If you want to use `pt.io.get_new_storage_dir` to manage your experiments, you have to define an environment variable `STORAGE_ROOT` which points to the path where all your experiments will be stored, i.e., in the example above, a new directory under `$STORAGE_ROOT/my_experiment_1` will be created.
Otherwise, you can use `pt.io.get_new_subdir` where you can directly input the path to store your model without defining an environment variable.

## Further Reading

Have a look at the following links to get the most out of your experience with padertorch:

- [Configurable](doc/configurable.md): Provides a thorough explanation of our [configurable](padertorch/configurable.py) module which allows to create model instances from a config dict.
- [Sacred](doc/sacred.md): Explains how to use sacred in combination with `Configurable`.
- [contrib/](padertorch/contrib): Unordered collection of advanced and experimental features. Subject to breaking changes so be careful with relying on it too much. But it might provide ideas for your own implementations.
- [contrib/examples/](padertorch/contrib/examples): Shows advanced usage of padertorch with actual data and models. A good starting point to get ideas for writing own models and experiments. See [here](doc/examples.md) for a guide to navigate through the examples and recommendations for which examples to start with.
