# Copyright 2021 The QHBM Library Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
"""Tools for inference on energy functions represented by a BitstringEnergy."""

import abc
import functools
import itertools
from typing import Union

import tensorflow as tf
import tensorflow_probability as tfp
from tensorflow_probability import distributions as tfd

from qhbmlib.models import energy
from qhbmlib import utils


def preface_inference(f):
  """Wraps given function with things to run before every inference call.

  Args:
    f: The method of `EnergyInference` to wrap.

  Returns:
    wrapper: The wrapped function.
  """

  @functools.wraps(f)
  def wrapper(self, *args, **kwargs):
    self._preface_inference()  # pylint: disable=protected-access
    return f(self, *args, **kwargs)

  return wrapper


class EnergyInferenceBase(tf.keras.layers.Layer, abc.ABC):
  r"""Defines the interface for inference on BitstringEnergy objects.

  Let $E$ be the energy function defined by a given `BitstringEnergy`, and let
  $X$ be the set of bitstrings in the domain of $E$.  Associated with $E$ is
  a probability distribution
  $$p(x) = \frac{e^{-E(x)}}{\sum_{y\in X} e^{-E(y)}},$$
  which we call the Energy Based Model (EBM) associated with $E$.  Inference
  in this class means estimating quantities of interest relative to the EBM.
  """

  def __init__(self,
               input_energy: energy.BitstringEnergy,
               initial_seed: Union[None, tf.Tensor] = None,
               name: Union[None, str] = None):
    """Initializes an EnergyInferenceBase.

    Args:
      input_energy: The parameterized energy function which defines this
        distribution via the equations of an energy based model.  This class
        assumes that all parameters of `energy` are `tf.Variable`s and that
        they are all returned by `energy.variables`.
      initial_seed: PRNG seed; see tfp.random.sanitize_seed for details. This
        seed will be used in the `sample` method.  If None, the seed is updated
        after every inference call.  Otherwise, the seed is fixed.
      name: Optional name for the model.
    """
    super().__init__(name=name)
    self._energy = input_energy
    self._energy.build([None, self._energy.num_bits])

    self._tracked_variables = input_energy.variables
    if len(self._tracked_variables) == 0:
      self._checkpoint = False
    else:
      self._tracked_variables_checkpoint = [
          tf.Variable(v.read_value(), trainable=False)
          for v in self._tracked_variables
      ]
      self._checkpoint = True

    if initial_seed is None:
      self._update_seed = tf.Variable(True, trainable=False)
    else:
      self._update_seed = tf.Variable(False, trainable=False)
    self._seed = tf.Variable(
        tfp.random.sanitize_seed(initial_seed), trainable=False)
    self._first_inference = tf.Variable(True, trainable=False)

  @property
  def energy(self):
    """The energy function which sets the probabilities for this EBM."""
    return self._energy

  @property
  def seed(self):
    """Current TFP compatible seed controlling sampling behavior.

    PRNG seed; see tfp.random.sanitize_seed for details. This seed will be used
    in the `sample` method.  If None, the seed is updated after every inference
    call.  Otherwise, the seed is fixed.
    """
    return self._seed

  @seed.setter
  def seed(self, initial_seed: Union[None, tf.Tensor]):
    """Sets a new value of the random seed.

    Args:
      initial_seed: see `self.seed` for details.
    """
    if initial_seed is None:
      self._update_seed.assign(True)
    else:
      self._update_seed.assign(False)
    self._seed.assign(tfp.random.sanitize_seed(initial_seed))

  @property
  def variables_updated(self):
    """Returns True if tracked variables do not have the checkpointed values."""
    if self._checkpoint:
      variables_not_equal_list = tf.nest.map_structure(
          lambda v, vc: tf.math.reduce_any(tf.math.not_equal(v, vc)),
          self._tracked_variables, self._tracked_variables_checkpoint)
      return tf.math.reduce_any(tf.stack(variables_not_equal_list))
    else:
      return False

  def _checkpoint_variables(self):
    """Checkpoints the currently tracked variables."""
    if self._checkpoint:
      tf.nest.map_structure(lambda v, vc: vc.assign(v), self._tracked_variables,
                            self._tracked_variables_checkpoint)

  def _preface_inference(self):
    """Things all energy inference methods do before proceeding.

    Called by `preface_inference` before the wrapped inference method.
    Currently includes:
      - run `self._ready_inference` if this is first call of a wrapped function
      - change the seed if not set by the user during initialization
      - run `self._ready_inference` if tracked energy parameters changed

    Note: subclasses should take care to call the superclass method.
    """
    if self._first_inference:
      self._checkpoint_variables()
      self._ready_inference()
      self._first_inference.assign(False)
    if self._update_seed:
      new_seed, _ = tfp.random.split_seed(self.seed)
      self._seed.assign(new_seed)
    if self.variables_updated:
      self._checkpoint_variables()
      self._ready_inference()

  @abc.abstractmethod
  def _ready_inference(self):
    """Performs computations common to all inference methods.

    Contains inference code that must be run first if the variables of
    `self.energy` have been updated since the last time inference was performed.
    """

  @preface_inference
  def call(self, inputs, *args, **kwargs):
    """Calls this layer on the given inputs."""
    return self._call(inputs, *args, **kwargs)

  @preface_inference
  def entropy(self):
    """Returns an estimate of the entropy."""
    return self._entropy()

  @preface_inference
  def expectation(self, function):
    """Returns an estimate of the expectation value of the given function.

    Args:
      function: Mapping from a 2D tensor of bitstrings to a possibly nested
        structure.  The structure must have atomic elements all of which are
        float tensors with the same batch size as the input bitstrings.
    """
    return self._expectation(function)

  @preface_inference
  def log_partition(self):
    """Returns an estimate of the log partition function."""
    return self._log_partition()

  @preface_inference
  def sample(self, num_samples: int):
    """Returns samples from the EBM corresponding to `self.energy`.

    Args:
      num_samples: Number of samples to draw from the EBM.
    """
    return self._sample(num_samples)

  @abc.abstractmethod
  def _call(self, inputs, *args, **kwargs):
    """Default implementation wrapped by `self.call`."""
    raise NotImplementedError()

  @abc.abstractmethod
  def _entropy(self):
    """Default implementation wrapped by `self.entropy`."""
    raise NotImplementedError()

  @abc.abstractmethod
  def _expectation(self, function):
    """Default implementation wrapped by `self.expectation`."""
    raise NotImplementedError()

  @abc.abstractmethod
  def _log_partition(self):
    """Default implementation wrapped by `self.log_partition`."""
    raise NotImplementedError()

  @abc.abstractmethod
  def _sample(self, num_samples: int):
    """Default implementation wrapped by `self.sample`."""
    raise NotImplementedError()


class EnergyInference(EnergyInferenceBase):
  """Provides some default method implementations."""

  def __init__(self,
               input_energy: energy.BitstringEnergy,
               num_expectation_samples: int,
               initial_seed: Union[None, tf.Tensor] = None,
               name: Union[None, str] = None):
    """Initializes an EnergyInference.

    Args:
      input_energy: The parameterized energy function which defines this
        distribution via the equations of an energy based model.  This class
        assumes that all parameters of `energy` are `tf.Variable`s and that
        they are all returned by `energy.variables`.
      num_expectation_samples: Number of samples to draw and use for estimating
        the expectation value.
      initial_seed: PRNG seed; see tfp.random.sanitize_seed for details. This
        seed will be used in the `sample` method.  If None, the seed is updated
        after every inference call.  Otherwise, the seed is fixed.
      name: Optional name for the model.
    """
    super().__init__(input_energy, initial_seed, name)
    self.num_expectation_samples = num_expectation_samples

  def _expectation(self, function):
    """Default implementation wrapped by `self.expectation`.

    Estimates an expectation value using sample averaging.
    """

    @tf.custom_gradient
    def _inner_expectation():
      """Enables derivatives."""
      samples = tf.stop_gradient(self.sample(self.num_expectation_samples))
      bitstrings, _, counts = utils.unique_bitstrings_with_counts(samples)

      # TODO(#157): try to parameterize the persistence.
      with tf.GradientTape() as values_tape:
        # Adds variables in `self.energy` to `variables` argument of `grad_fn`.
        values_tape.watch(self.energy.trainable_variables)
        values = function(bitstrings)
        average_of_values = tf.nest.map_structure(
            lambda x: utils.weighted_average(counts, x), values)

      def grad_fn(*upstream, variables):
        """See equation A5 in the QHBM paper appendix for details.

        # TODO(#119): confirm equation number.

        The `values_tape.gradient` call is supposed to compute the last summand
        in equation A5.  To confirm that the sum over `i` is accounted for,
        I am doing a manual tracing of the calls made. See also
        "Gradients of non-scalar targets" section in the following link:
        https://www.tensorflow.org/guide/autodiff
        
        Let's walk through the calls that are made.  I'll write the function
        we are in and the file that function resides in, followed by
        salient computations performed there:
        
        1) `gradient` in tensorflow/python/eager/backprop.py
           Args `targets`, `sources`, and `output_gradients` each get flattened
        
        2) `imperative_grad` in tensorflow/python/eager/imperative_grad.py
           Light wrapper around `TFE_Py_TapeGradient`

        3) `TFE_Py_TapeGradient` in tensorflow/python/eager/pywrap_tfe_src.cc
           Vector of `PyObject` pointers is created, with the same number of
           entries as there are atomic elements in `variables`.  This is
           handed to a call to `tape_obj->tape->ComputeGradient`, so that
           function can store final gradient results in it.
        
        4) `ComputeGradient` in tensorflow/c/eager/tape.h
           Creates map of vectors of `Gradient` pointers.
           From the docs, this `Gradient` type is described as:
           "Gradient is the type returned by gradient functions.
           In Python TF it's either Tensor or IndexedSlices or None
           which here we map to nullptr."
           This map will be keyed by tensor IDs of both targets and sources.
           This map is named `gradients`.
        
        5) `InitialGradients` in tensorflow/c/eager/tape.h
           This call pushes back `output_gradients[i]` onto the vector
           of `Gradient` pointers keyed by the ID of `average_of_values[i]`.
        
        6) back inside `ComputeGradient`
           While loop over remaining stack of unprocessed graph ops.
           In the loop, make a note of which source and target tensors appear
           in the op computation.  Make a new vector of `Gradient` pointers,
           called `in_gradients`.

        7) `CallBackwardFunction` in tensorflow/python/eager/pywrap_tfe_src.cc
           The gradient to source is computed and recorded on `in_gradients`.
        
        8) back inside `ComputeGradient`
           For each input tensor, append the result from `in_gradients[i]`
           to the vector in `gradients` keyed by that input tensor's ID.
           After all the ops have been processed by the while loop of step (6),
           start putting gradients into the final vector of gradients which was
           created back in step (3).

           My understanding of the current state at this point:
           *****
           `gradients` contains a separate vector of backpropagated derivatives
           for each entry `j` of thetas.  The vector holds each entry `i` of the
           last summand in equation A5 for that particular `j`.
           *****
           
           Now the check on the sum assumption.  See this link:
           https://github.com/tensorflow/tensorflow/blob/3f878cff5b698b82eea85db2b60d65a2e320850e/tensorflow/c/eager/tape.h#L878
           If there is more than one entry in the gradient vector for a given
           source, `AggregateGradients` is called on it.

        9) `AggregateGradients` of tensorflow/python/eager/pywrap_tfe_src.cc
           `aggregate_fn_` is called, which is a wrapper on `aggregate_fn`

        10) `VSpace` in tensorflow/python/eager/backprop.py
           We see that `aggregate_fn` is set to `_aggregate_grads`.
           `_aggregate_grads` calls `add_n`

        11) `add_n` in tensorflow/python/ops/math_ops.py
           Docstring says, "Adds all input tensors element-wise".

        So, the summation over i in the last summand of equation A5 seems to be
        occurring as expected.
        """
        ####
        # Preliminary calculations.

        # d g / d <f_i>
        # This is a list where `i` indexes the atomic elements of `upstream`.
        # Note `upstream` has the same structure as `average_of_values`.
        dg_dfi = tf.nest.flatten(upstream)

        # Compute grad E terms.
        with tf.GradientTape() as tape:
          energies = self.energy(bitstrings)
        # d E_theta(x) / d theta_j
        # Returned value is a list, one entry for each variable.
        # Each entry is a tensor; first index x is the bitstring, remaining
        # indices are the same as the corresponding entry of `variables`
        energies_grads = tape.jacobian(
            energies,
            variables,
            unconnected_gradients=tf.UnconnectedGradients.ZERO)

        ####
        # Compute first summand in equation A5.

        # <f_i>
        fi = tf.nest.flatten(average_of_values)

        # Multiply d g / d <f_i> times <f_i> for each i
        dg_dfi_times_fi = tf.nest.map_structure(lambda x, y: x * y, dg_dfi, fi)

        # Summing over i first requires summing over all inner indices.
        # in the notes is only the outermost summation over i is shown.
        sum_dg_dfi_times_fi = tf.nest.map_structure(lambda x: tf.reduce_sum(x), dg_dfi_times_fi)

        # Do the outer sum over i.  This is now a scalar.
        i_sum_dg_dfi_times_fi = tf.reduce_sum(tf.stack(sum_dg_dfi_times_fi), 0)        

        # d <E_theta> / d theta_j
        average_of_energies_grads = tf.nest.map_structure(
            lambda x: utils.weighted_average(counts, x), energies_grads)
        first_summand = tf.nest.map_structure(lambda x: x * i_sum_dg_dfi_times_fi, average_of_energies_grads)

        ####
        # Compute middle summand in equation A5.

        # fi(x), the ith entry of f computed against bitstring x
        fi_x = tf.nest.flatten(values)

        # The multiplication takes advantage of broadcast rules.
        # Here, all the rightmost dimensions of `x` and `y` are equal, since
        # for each pair of atomic elements `x` and `y`, we have
        # tf.shape(x) == tf.shape(y)[1:], and
        # tf.shape(y)[0] == tf.shape(bitstrings)[0].
        # Thus `x` is multiplied against each row of `y`.
        combined_flat = tf.nest.map_structure(lambda x, y: x * y, dg_dfi, fi_x)

        # Sum out inner indices; map over bitstrings to preserve batch dimension
        combined_flat_sum = tf.nest.map_structure(
            lambda x: tf.map_fn(tf.reduce_sum, x), combined_flat)

        # Sum over i, leaving 1D tensor, a scalar at each bitstring.
        combined_sum = tf.reduce_sum(tf.stack(combined_flat_sum), 0)

        # multiply by the energy derivative at each bitstring
        energy_times_combined_sum = tf.nest.map_structure(
            lambda x: tf.einsum("i...,i->i...", x, combined_sum),
            energies_grads)

        middle_summand = tf.nest.map_structure(
            lambda x: utils.weighted_average(counts, x), energy_times_combined_sum)

        ####
        # Last summand in equation A5.
        # `output_gradients` is  d g / d <f_i>
        # See discussion in the docstring for details.
        last_summand = values_tape.gradient(
            average_of_values,
            variables,
            output_gradients=upstream,
            unconnected_gradients=tf.UnconnectedGradients.ZERO)

        # Note: upstream gradient is already a coefficient in fs, ms, and ls.
        return tuple(), [
            fs - ms + ls for fs, ms, ls in zip(
                first_summand, middle_summand, last_summand)
        ]

      return average_of_values, grad_fn

    return _inner_expectation()

  def _log_partition(self):
    """Default implementation wrapped by `self.log_partition`."""

    @tf.custom_gradient
    def _inner_log_partition():
      """Wraps forward pass computaton."""
      result = self._log_partition_forward_pass()
      # Adds variables in `self.energy` to `variables` argument of `grad_fn`.
      _ = [tf.identity(x) for x in self.energy.trainable_variables]
      grad_fn = self._log_partition_grad_generator()
      return result, grad_fn

    return _inner_log_partition()

  @abc.abstractmethod
  def _log_partition_forward_pass(self):
    """Returns approximation to the log partition function."""
    raise NotImplementedError()

  def _log_partition_grad_generator(self):
    """Returns default estimator for the log partition function derivative."""

    def grad_fn(upstream, variables):
      """See equation C2 in the appendix.  TODO(#119)"""

      def energy_grad(bitstrings):
        """Calculates the derivative with respect to the current variables."""
        with tf.GradientTape() as tape:
          energies = self.energy(bitstrings)
        jac = tape.jacobian(
            energies,
            variables,
            unconnected_gradients=tf.UnconnectedGradients.ZERO)
        return jac

      energy_grad_expectation_list = self.expectation(energy_grad)
      return tuple(), [
          upstream * (-1.0 * ege) for ege in energy_grad_expectation_list
      ]

    return grad_fn


class AnalyticEnergyInference(EnergyInference):
  """Uses an explicit categorical distribution to implement parent functions."""

  def __init__(self,
               input_energy: energy.BitstringEnergy,
               num_expectation_samples: int,
               initial_seed: Union[None, tf.Tensor] = None,
               name: Union[None, str] = None):
    """Initializes an AnalyticEnergyInference.

    Internally, this class saves all possible bitstrings as a tensor, whose
    energies are calculated relative to an input energy function for sampling
    and other inference tasks.

    Args:
      input_energy: The parameterized energy function which defines this
        distribution via the equations of an energy based model.  This class
        assumes that all parameters of `energy` are `tf.Variable`s and that
        they are all returned by `energy.variables`.
      num_expectation_samples: Number of samples to draw and use for estimating
        the expectation value.
      initial_seed: PRNG seed; see tfp.random.sanitize_seed for details. This
        seed will be used in the `sample` method.  If None, the seed is updated
        after every inference call.  Otherwise, the seed is fixed.
      name: Optional name for the model.
    """
    super().__init__(input_energy, num_expectation_samples, initial_seed, name)
    self._all_bitstrings = tf.constant(
        list(itertools.product([0, 1], repeat=input_energy.num_bits)),
        dtype=tf.int8)
    self._logits_variable = tf.Variable(
        -input_energy(self.all_bitstrings), trainable=False)
    self._distribution = tfd.Categorical(logits=self._logits_variable)

  @property
  def all_bitstrings(self):
    """Returns every bitstring."""
    return self._all_bitstrings

  @property
  def all_energies(self):
    """Returns the energy of every bitstring."""
    return self.energy(self.all_bitstrings)

  @property
  def distribution(self):
    """Categorical distribution set during `self._ready_inference`."""
    return self._distribution

  def _ready_inference(self):
    """See base class docstring."""
    self._logits_variable.assign(-self.all_energies)

  def _call(self, inputs, *args, **kwargs):
    """See base class docstring."""
    if inputs is None:
      return self.distribution
    else:
      return self.sample(inputs)

  def _entropy(self):
    """See base class docstring."""
    return self.distribution.entropy()

  def _log_partition_forward_pass(self):
    """See base class docstring."""
    # TODO(#115)
    return tf.reduce_logsumexp(self.distribution.logits_parameter())

  def _sample(self, num_samples: int):
    """See base class docstring."""
    return tf.gather(
        self.all_bitstrings,
        self.distribution.sample(num_samples, seed=self.seed),
        axis=0)


class BernoulliEnergyInference(EnergyInference):
  """Manages inference for a Bernoulli defined by spin energies."""

  def __init__(self,
               input_energy: energy.BernoulliEnergy,
               num_expectation_samples: int,
               initial_seed: Union[None, tf.Tensor] = None,
               name: Union[None, str] = None):
    """Initializes a BernoulliEnergyInference.

    Args:
      input_energy: The parameterized energy function which defines this
        distribution via the equations of an energy based model.  This class
        assumes that all parameters of `energy` are `tf.Variable`s and that
        they are all returned by `energy.variables`.
      num_expectation_samples: Number of samples to draw and use for estimating
        the expectation value.
      initial_seed: PRNG seed; see tfp.random.sanitize_seed for details. This
        seed will be used in the `sample` method.  If None, the seed is updated
        after every inference call.  Otherwise, the seed is fixed.
      name: Optional name for the model.
    """
    super().__init__(input_energy, num_expectation_samples, initial_seed, name)
    self._logits_variable = tf.Variable(input_energy.logits, trainable=False)
    self._distribution = tfd.Bernoulli(
        logits=self._logits_variable, dtype=tf.int8)

  @property
  def distribution(self):
    """Bernoulli distribution set during `self._ready_inference`."""
    return self._distribution

  def _ready_inference(self):
    """See base class docstring."""
    self._logits_variable.assign(self.energy.logits)

  def _call(self, inputs, *args, **kwargs):
    """See base class docstring."""
    if inputs is None:
      return self.distribution
    else:
      return self.sample(inputs)

  def _entropy(self):
    """Returns the exact entropy.

    The total entropy of a set of spins is the sum of each individual spin's
    entropies.
    """
    return tf.reduce_sum(self.distribution.entropy())

  def _log_partition_forward_pass(self):
    r"""Returns the exact log partition function.

    For a single spin of energy $\theta$, the partition function is
    $$Z_\theta = \exp(\theta) + \exp(-\theta).$$
    Since each spin is independent, the total log partition function is
    the sum of the individual spin log partition functions.
    """
    thetas = 0.5 * self.energy.logits
    single_log_partitions = tf.math.log(
        tf.math.exp(thetas) + tf.math.exp(-thetas))
    return tf.math.reduce_sum(single_log_partitions)

  def _sample(self, num_samples: int):
    """See base class docstring"""
    return self.distribution.sample(num_samples, seed=self.seed)
