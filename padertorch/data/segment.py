from copy import copy
from typing import Union

import numpy as np
import torch
from paderbox.array import segment_axis
from paderbox.utils.nested import flatten, deflatten
from padertorch.utils import to_list

possible_anchor_modes = [
    'left',
    'right',
    'center',
    'centered_cutout',
    'random',
    'random_max_segments',
]

possible_length_modes = [
    'max', 'min', 'constant'
]

class Segmenter:
    """
    This segmenting returns a list of segmented examples that can be unbatched.
    Everything that is not listed in `keys` is simply copied from the
    input example to the output examples.
    The keys `segment_start` and `segment_stop` are added for each output
    dictionary.

    If an utterance is shorter than `length`, a
    `lazy_dataset.FilterException` is raised.

    Examples (For more examples see `tests/test_data/test_segmenter`):

        >>> segmenter = Segmenter(length=32000, include_keys=('x', 'y'),
        ...                           shift=16000)
        >>> ex = {'x': np.arange(65000), 'y': np.arange(65000),
        ...       'num_samples': 65000, 'gender': 'm'}
        >>> segmented = segmenter(ex)
        >>> type(segmented)
        <class 'list'>
        >>> for entry in segmented:
        ...     print(entry['x'][[0, -1]])
        [    0 31999]
        [16000 47999]
        [32000 63999]

        Segmenting can be disabled by setting `length=-1`.
        >>> Segmenter(length=-1, include_keys=('x', 'y'))(ex)[0] == ex
        True

        Check the corner cases.
        >>> ex = {'x': np.arange(64000), 'y': np.arange(64000)}
        >>> for entry in segmenter(ex):
        ...     print(entry['x'][[0, -1]])
        [    0 31999]
        [16000 47999]
        [32000 63999]
        >>> ex = {'x': np.arange(63999), 'y': np.arange(63999)}
        >>> for entry in segmenter(ex):
        ...     print(entry['x'][[0, -1]])
        [    0 31999]
        [16000 47999]

    Args:
        length: The length of the segments in samples. If set to `-1`,
            the original example is returned in a list of length one.
        shift: shift between segments, defaults to length
        include_keys: The keys in the passed example dict to segment. They all
            must have the same size along their specified `axis`, if keys is
            `None` the segmentation is applied to all `numpy.arrays`.
            If a key points to a dictionary the segmentation is applied to all
            nested values of this dictionary
        exclude_keys: This option allows to specify specific keys not to
            segment. This might be usefull if `include_keys` is `None` or
            one of the included keys points to a dictionary.
        copy_keys: If `True` all values which are not segmented are
            copied. If `False` the new dictionary only consists of the
            segmented keys are copied. Otherwise only the specified keys are
            added to the new dictionary with the segmented signals.
        axis: axis over which to segment. Maybe a `list`, a `dict` or a `int`.
            In case of `list` the length has to be equal to `include_keys`.
            I case of `dict` the keys have to be equal to
            the entries of `include_keys`
        anchor: anchor or anchor mode used in `get_anchor` to calculate
            the anchor from which the segmentation boundaries are calculated.
        length_mode: used in _get_segment_length_for_mode
        flatten_separator: specifies the separator used to separate the keys
            in the flattened dictionary. Defaults to `.`
    """

    def __init__(self, length: int = -1, shift: int = None,
                 include_keys: Union[str, list, tuple] = None,
                 exclude_keys: Union[str, list, tuple] = None,
                 copy_keys: Union[str, bool, list, tuple] = True,
                 axis: Union[int, list, tuple, dict] = -1,
                 anchor: Union[int, str] = 'left',
                 length_mode: 'str' = 'constant',
                 flatten_separator: str = '.'):

        self.include = None if include_keys is None else to_list(include_keys)
        self.exclude = [] if exclude_keys is None else to_list(exclude_keys)
        self.length = length
        if isinstance(axis, (dict, int)):
            self.axis = axis
            if isinstance(axis, dict):
                assert self.include is not None, (self.axis, self.include)
                assert set(axis.keys()) == set(self.include), (
                    axis.keys(), self.include
                )
        elif isinstance(axis, (tuple, list)):
            self.axis = to_list(axis)
            assert self.include is not None, (self.axis, self.include)
            assert len(axis) == len(include_keys), (
                'If axis are specified as list it has to have the same length'
                'as include_keys', axis, include_keys
            )
        else:
            raise TypeError('Unknown type for axis', axis)
        self.shift = shift
        assert isinstance(anchor, (str, int)), anchor
        self.anchor = anchor
        self.copy_keys = to_list(copy_keys)
        assert all([isinstance(key, (bool, str)) for key in self.copy_keys]), (
            'All keys in copy_keys have to be str, or copy key has to be one'
            'boolean', copy_keys
        )
        assert length_mode in possible_length_modes, (
            'length_mode has to be one of', possible_length_modes,
            'but is', length_mode
        )
        self.length_mode = length_mode
        self.flatten_separator = flatten_separator

    def __call__(self, example, rng=np.random):
        """

        Args:
            example: dictionary with string keys
            rng: random number generator, maybe set using
                paderbox.utils.random_utils.str_to_random_state

        Returns:

        """
        # Shortcut if segmentation is disabled
        if self.length == -1:
            return [example]

        example = flatten(example, sep=self.flatten_separator)

        to_segment_keys = self.get_to_segment_keys(example)
        axis = self.get_axis_list(to_segment_keys)

        to_segment = {
            key: example.pop(key) for key in to_segment_keys
        }

        if any([not isinstance(value, (np.ndarray, torch.Tensor))
                for value in to_segment.values()]):
            raise ValueError(
                'This segmenter only works on numpy arrays',
                'However, the following keys point to other types:',
                '\n'.join([f'{key} points to a {type(to_segment[key])}'
                           for key in to_segment_keys])
            )

        to_segment_lengths = [
            v.shape[axis[i]] for i, v in enumerate(to_segment.values())]
        assert to_segment_lengths[1:] == to_segment_lengths[:-1], (
            'The shapes along the segment dimension of all entries to segment'
            ' must be equal!\n'
            f'segment keys: {to_segment_keys}'
            f'to_segment_lengths: {to_segment_lengths}'
        )
        assert len(to_segment) > 0, ('Did not find any signals to segment',
                                     self.include, self.exclude, to_segment)
        to_segment_length = to_segment_lengths[0]

        # Discard examples that are shorter than `length`
        if to_segment_length < self.length:
            import lazy_dataset
            raise lazy_dataset.FilterException()

        boundaries, segmented = self.segment(to_segment, to_segment_length,
                                             axis=axis, rng=rng)

        segmented_examples = list()
        if all([isinstance(key, str) for key in self.copy_keys]):
            example = {key: example.pop(key) for key in self.copy_keys}
        elif self.copy_keys[0] is True:
            assert len(self.copy_keys) == 1, self.copy_keys
            pass
        elif self.copy_keys[0] is False:
            assert len(self.copy_keys) == 1, self.copy_keys
            example = {}
        else:
            raise TypeError('Unknown type for copy keys', self.copy_keys)

        for idx, (start, stop) in enumerate(boundaries):
            example_copy = copy(example)
            example_copy.update({key: value[idx]
                                 for key, value in segmented.items()})
            example_copy.update(segment_start=start, segment_stop=stop)
            segmented_examples.append(deflatten(example_copy))
        return segmented_examples

    def segment(self, to_segment, to_segment_length, axis, rng=np.random):
        """
        >>> import numpy as np
        >>> ex = {'x': np.arange(16000), 'y': np.arange(16000)}
        >>> segmenter = Segmenter(length=950, include_keys=('x'),
        ...                       length_mode='max')
        >>> boundaries, segmented = segmenter.segment(ex, 16000, [0, 0])
        >>> len(boundaries), len(segmented['x'])
        (17, 17)
        """
        length, shift, to_segment_length = _get_segment_length_for_mode(
            to_segment_length, self.length, self.shift, self.length_mode)

        if isinstance(self.anchor, str):
            anchor = get_anchor(
                to_segment_length, length, shift,
                mode=self.anchor, rng=rng
            )
        else:
            assert isinstance(self.anchor, int), self.anchor
            anchor = self.anchor
        boundaries = get_segment_boundaries(
            to_segment_length, length, shift, anchor=self.anchor, rng=rng
        )

        segmented = {key: segment(signal, length=length, shift=shift,
                                  rng=rng, axis=axis[i], anchor=anchor,
                                  length_mode=self.length_mode)
                     for i, (key, signal) in enumerate(to_segment.items())}
        return boundaries, segmented

    def get_to_segment_keys(self, example):
        if self.include is None:
            return [
                key for key, value in example.items()
                if key not in self.exclude and
                   isinstance(value, (np.ndarray, torch.Tensor))
            ]
        else:
            to_segment_keys = [
                key for key in example.keys() if
                key not in self.exclude and
                any([key.startswith(include_key)
                     for include_key in self.include])
            ]
            assert all([
                any([key.startswith(include_key) for key in to_segment_keys])
                for include_key in self.include
            ]), ('For some keys in include_keys no associated key was found '
                 'in the example', self.include, to_segment_keys)
            return to_segment_keys

    def get_axis_list(self, to_segment_keys):
        if isinstance(self.axis, int):
            return [self.axis] * len(to_segment_keys)
        elif isinstance(self.axis, dict):
            axis = self.axis.copy()
            axis.update({
                to_segment_key: axis for to_segment_key in to_segment_keys
                for org_key, axis in self.axis.items()
                if to_segment_key.startswith(org_key)
            })
            assert all([key in axis.keys() for key in to_segment_keys]), (
                f'The dictionary for axis did not include keys for all'
                f'segment arrays. axis keys: {self.axis.keys()},'
                f'segment array keys {to_segment_keys}'
            )
            return [axis[key] for key in to_segment_keys]
        elif isinstance(self.axis, list):
            assert len(self.axis) == len(to_segment_keys), (
                f'The list for axis does not include a axis for each'
                f'segment array. axis: {self.axis}, '
                f'segment array keys {to_segment_keys}'
            )
            return self.axis
        else:
            raise TypeError('This should never be reached', self.axis)


def _get_rand_int(rng, *args, **kwargs):
    if hasattr(rng, 'randint'):
        return rng.randint(*args, **kwargs)
    elif hasattr(rng, 'integers'):
        return rng.integers(*args, **kwargs)
    elif isinstance(rng, callable):
        return rng(*args, **kwargs)
    else:
        raise TypeError('Unknown random generator used', rng)


def get_anchor(
        num_samples: int, length: int, shift: int = None,
        mode: str = 'left', rng=np.random
):
    """
    Calculates anchor for the boundaries for segmentation of a signal
    with length `num_sammples` in case of a fixed segment length
    `length` and shift `shift`. The anchor always points to the first value of
    a segment.

    Args:
        num_samples: num samples of signal for which boundaries are caclulated
        length: segment length
        shift: shift between segments, defaults to length
        mode: Defines the position of the boundaries in the signal:
            left: anchor is set to zero
                so that only values at the end are cut
            right: anchor is set to `num_samples`
                so that only values at the beginning are cut
            center: anchor is set to `num_samples // 2`
            centered_cutout: the anchor is chosen such that the same number
                of samples are discarded at the end and the beginning
            random: anchor is set to a random value between
                `0` and `num_samples`.
                 This may reduce the number of possible segments.
            random_max_segments: Randomly chooses the anchor such that
                the maximum number of segments are created
        rng: random number generator (`numpy.random`)

    Returns:
       integer value describing the anchor
    >>> np.random.seed(3)
    >>> get_anchor(24, 10, 3, mode='left')
    0
    >>> get_anchor(24, 10, 3, mode='right')
    14
    >>> get_anchor(24, 10, 3, mode='center')
    12
    >>> get_anchor(24, 10, 3, mode='centered_cutout')
    1
    >>> get_anchor(24, 10, 3, mode='random')
    10
    >>> get_anchor(24, 10, 3, mode='random', rng=np.random.default_rng(seed=4))
    10
    >>> get_anchor(24, 10, 3, mode='random_max_segments')
    3
    """
    assert num_samples >= length, (num_samples, length)
    if shift is None:
        shift = length
    assert shift > 0, shift

    if mode == 'left':
        return 0
    elif mode == 'right':
        return num_samples - length
    elif mode == 'center':
        return num_samples // 2
    elif mode == 'centered_cutout':
        remainder = (num_samples - length) % shift
        return remainder // 2
    elif mode == 'random':
        return _get_rand_int(rng, num_samples - length)
    elif mode == 'random_max_segments':
        start = _get_rand_int(rng, (num_samples - length) % shift + 1)
        anchors = np.arange(start, num_samples - length + 1, shift)
        return int(np.random.choice(anchors))
    else:
        raise ValueError('Unknown mode', mode,
                         'choose on of', possible_anchor_modes)


def get_segment_boundaries(
        num_samples: int, length: int, shift: int = None,
        anchor: Union[str, int] = 'left', rng=np.random
):
    """
    Calculates boundaries for segmentation of a signal with length
    `num_samples` in case of a fixed segment length `length` and shift `shift`

    Args:
        num_samples: number of samples of signal for which
            boundaries are calculated.
        length: segment length
        shift: shift between segments, defaults to length
        anchor: anchor from which the segmentation boundaries are calculated.
            If it is a string `get_anchor` is called to calculate an integer
            using `anchor` as anchor mode definition.
        rng: random number generator (`numpy.random`)

    Returns:
        Bx2 numpy array with start and end values for B boundaries

    >>> np.random.seed(3)
    >>> get_segment_boundaries(24, 10, 3, anchor='left').T
    array([[ 0,  3,  6,  9, 12],
           [10, 13, 16, 19, 22]])
    >>> get_segment_boundaries(24, 10, 3, anchor='right').T
    array([[ 2,  5,  8, 11, 14],
           [12, 15, 18, 21, 24]])
    >>> get_segment_boundaries(24, 10, 3, anchor='center').T
    array([[ 0,  3,  6,  9, 12],
           [10, 13, 16, 19, 22]])
    >>> get_segment_boundaries(24, 10, 3, anchor='centered_cutout').T
    array([[ 1,  4,  7, 10, 13],
           [11, 14, 17, 20, 23]])
    >>> get_segment_boundaries(24, 10, 3, anchor='random').T
    array([[ 1,  4,  7, 10, 13],
           [11, 14, 17, 20, 23]])
    >>> get_segment_boundaries(24, 10, 3, anchor='random_max_segments').T
    array([[ 0,  3,  6,  9, 12],
           [10, 13, 16, 19, 22]])
    """
    assert num_samples >= length, (num_samples, length)
    if shift is None:
        shift = length
    assert shift > 0, shift
    if isinstance(anchor, str):
        anchor = get_anchor(num_samples, length, shift, mode=anchor, rng=rng)
    assert isinstance(anchor, int), (anchor, type(anchor))

    start = anchor % shift
    start = np.arange(start, num_samples - length + 1, shift)
    stop = start + length
    boundaries = np.stack([start, stop], axis=-1)
    return boundaries


def _get_segment_length_for_mode(num_samples, length: int, shift: int = None,
                                 length_mode: str = 'constant'):
    """
    This function calculates an optimal segment length assuming that `length`
    is equal to the segment shift. Length can be used in three different ways
    depending on the length_mode.

    Args:
        num_samples: number of samples to be segmented
        length: segment length
        shift: shift between segments
        length_mode: constant: uses `length` for all examples
                     max:      uses some length smaller than `length` for each
                               example
                     min:      uses some length larger than `length` for each
                               example
            max and min calculate a length with minimum padding, in case of
            constant mode the residual samples are cut.
    Returns:
        Tuple of adapted segment length and shift as `int` and number of
        samples after padding in case of `length_mode` equal to max or min.
        Otherwise return `num_samples`

    >>> length = 1000; num_samples = 16000
    >>> _get_segment_length_for_mode(num_samples, length)
    (1000, 1000, 16000)
    >>> _get_segment_length_for_mode(num_samples, length, None, 'max')
    (1000, 1000, 16000)
    >>> _get_segment_length_for_mode(num_samples, length, None, 'min')
    (1000, 1000, 16000)
    >>> length = 950; num_samples = 16000
    >>> _get_segment_length_for_mode(num_samples, length)
    (950, 950, 16000)
    >>> _get_segment_length_for_mode(num_samples, length, None, 'max')
    (942, 942, 16014)
    >>> _get_segment_length_for_mode(num_samples, length, None, 'min')
    (1000, 1000, 16000)
    >>> length = 950; shift = 250; num_samples = 16000
    >>> _get_segment_length_for_mode(num_samples, length, shift)
    (950, 250, 16000)
    >>> _get_segment_length_for_mode(num_samples, length, shift, 'max')
    (947, 247, 16014)
    >>> _get_segment_length_for_mode(num_samples, length, shift, 'min')
    (950, 250, 15950)
    """

    if shift is None:
        shift = length
    if length_mode == 'constant':
        return length, shift, num_samples
    elif length_mode in ['min', 'max']:
        overlap = length - shift
        if length_mode == 'max':
            n = (num_samples - 1 - overlap) // (length - overlap) + 1
            new_length = (num_samples - 1 - overlap) // n + 1 + overlap
        else:
            n = (num_samples - overlap) // (length - overlap)
            new_length = ((num_samples - overlap) % (length - overlap)) // n
            new_length = new_length + length
        shift = new_length - overlap
        padded_length = (n - 1) * shift + new_length
        return new_length, shift, padded_length
    else:
        raise ValueError(length_mode, possible_length_modes)


def segment(
        x, length: int, shift: int = None, anchor: Union[str, int] = 'left',
        axis: int = -1, length_mode: str = 'constant', rng=np.random
):
    """
    Segments a signal `x` along an axis. Either with a predefined anchor for
    the segment boundaries if anchor is set or with an internally calculated
    anchor if anchor is a string.

    Args:
        x: signal to be segmented
        anchor: anchor from which the segmentation boundaries are calculated.
            if it is a string `get_anchor` is called to calculate an integer
            using `anchor` as anchor mode definition.
        length: segment length
        shift: shift between segments, defaults to length
        axis: axis over which to segment
        length_mode: used in _get_segment_length_for_mode
        rng: random number generator (`numpy.random`)

    Returns:

    >>> np.random.seed(3)
    >>> segment(np.arange(0, 15), 10, 3, anchor='left')
    array([[ 0,  1,  2,  3,  4,  5,  6,  7,  8,  9],
           [ 3,  4,  5,  6,  7,  8,  9, 10, 11, 12]])
    >>> segment(np.arange(0, 15), 10, 3, anchor='random')
    array([[ 2,  3,  4,  5,  6,  7,  8,  9, 10, 11],
           [ 5,  6,  7,  8,  9, 10, 11, 12, 13, 14]])
    >>> segment(np.arange(0, 15), 10, 3, anchor=5)
    array([[ 2,  3,  4,  5,  6,  7,  8,  9, 10, 11],
           [ 5,  6,  7,  8,  9, 10, 11, 12, 13, 14]])
    """

    if x.__class__.__module__ == 'numpy':
        ndim = x.ndim
        moveaxis = np.moveaxis
    elif x.__class__.__module__ == 'torch':
        ndim = x.dim()
        from distutils.version import LooseVersion

        if LooseVersion(torch.__version__) >= '1.7.0':
            moveaxis = torch.movedim
        else:
            # moveaxis code taken from
            # https: // github.com / pytorch / pytorch / issues / 36048
            def moveaxis(tensor: torch.Tensor, source: int,
                          destination: int) -> torch.Tensor:
                dim = tensor.dim()
                perm = list(range(dim))
                if destination < 0:
                    destination += dim
                perm.pop(source)
                perm.insert(destination, source)
                return tensor.permute(*perm)
    elif isinstance(x, list):
        x = np.array(x)
        ndim = x.ndim
        moveaxis = np.moveaxis
    else:
        raise TypeError('Unknown type for input signal x', type(x))
    axis = axis % ndim

    num_samples = x.shape[axis]
    assert num_samples >= length, (num_samples, length)
    if length_mode == 'constant':
        end = 'cut'
    elif length_mode in ['max', 'min']:
        end = 'pad'
    else:
        raise ValueError('Unknown length mode. Length mode has to be chosen'
                         'from', possible_length_modes, 'and is', length_mode)

    length, shift, num_samples = _get_segment_length_for_mode(
        num_samples, length, shift, length_mode)

    assert shift > 0, shift
    if isinstance(anchor, str):
        anchor = get_anchor(num_samples, length, shift, mode=anchor, rng=rng)
    assert isinstance(anchor, int), (anchor, type(anchor))

    start = anchor % shift

    # slice the array to remove samples discarded with the specified anchor
    slc = [slice(None)] * ndim
    slc[axis] = slice(start, None)
    x = x[tuple(slc)]

    return moveaxis(
        segment_axis(x, length, shift, end=end, axis=axis), axis, 0)
