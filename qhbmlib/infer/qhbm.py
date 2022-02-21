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
"""Tools for inference on quantum Hamiltonians."""

import functools
from typing import Union

import tensorflow as tf

from qhbmlib.infer import ebm
from qhbmlib.infer import qnn
from qhbmlib.model import hamiltonian
from qhbmlib import utils


class QHBM(tf.keras.layers.Layer):
  r"""Methods for inference involving normalized exponentials of Hamiltonians.

  We also call the normalized exponential of a Hamiltonian a "thermal state".
  Here we formalize some aspects of thermal states, which will be used later
  to explain particular methods of this class.

  # TODO(#119): add reference to updated QHBM paper.

  Each method takes as input some modular Hamiltonian
  $$K_{\theta\phi} = U_\phi K_\theta U_\phi^\dagger.$$
  The [thermal state][1] corresponding to the model is
  $$ \rho_T = Z^{-1} e^{-\beta K_{\theta\phi}}.$$
  For QHBMs, we assume $\beta = 1$, effectively absorbing it into the definition
  of the modular Hamiltonian.  Then $\rho_T$ can be expanded as
  $$\rho_T = \sum_x p_\theta(x)U_\phi\ket{x}\bra{x}U_\phi^\dagger,$$
  where the probability is given by
  $$p_\theta(x) = \tr[\exp(-K_\theta)]\bra{x}\exp(-K_\theta)\ket{x}$$
  for $x\in\{1, \ldots, \dim(K_{\theta\phi})\} = \mathcal{X}$. Note that each
  $U_\phi\ket{x}$ is an eigenvector of both $\rho_T$ and $K_{\theta\phi}$.

  Corresponding to this density operator is an [ensemble of quantum states][2].
  Using the terms above, we define the particular ensemble
  $$\mathcal{E} = \{p_\theta(x), U_\phi\ket{x}\}_{x\in\mathcal{X}},$$
  also known as the [canonical ensemble][2] corresponding to $\rho_T$.
  Each method of this class implicitly samples from this ensemble, then
  post-processes to perform a particular inference task.

  #### References
  [1]: Nielsen, Michael A. and Chuang, Isaac L. (2010).
       Quantum Computation and Quantum Information.
       Cambridge University Press.
  [2]: Wilde, Mark M. (2017).
       Quantum Information Theory (second edition).
       Cambridge University Press.
  """

  def __init__(self,
               input_ebm: ebm.EnergyInference,
               input_qnn: qnn.QuantumInference,
               name: Union[None, str] = None):
    """Initializes a QHBM.

    Args:
      input_ebm: Attends to density operator eigenvalues.
      input_qnn: Attends to density operator eigenvectors.
      name: Optional name for the model.
    """
    super().__init__(name=name)
    self._ebm = input_ebm
    self._qnn = input_qnn
    self._modular_hamiltonian = hamiltonian.Hamiltonian(self.ebm.energy,
                                                        self.qnn.circuit)

  @property
  def ebm(self):
    """The object used for inference on density operator eigenvalues."""
    return self._ebm

  @property
  def qnn(self):
    """The object used for inference on density operator eigenvectors."""
    return self._qnn

  @property
  def modular_hamiltonian(self):
    """The modular Hamiltonian defining this QHBM."""
    return self._modular_hamiltonian

  def circuits(self, num_samples: int):
    r"""Draws thermally distributed eigenstates from the model Hamiltonian.

    Here we explain the algorithm.  First, construct $X$ to be a classical
    random variable with probability distribution $p_\theta(x)$ set by
    `model.modular_hamiltonian.energy`.  Then, draw $n = $`num\_samples`
    bitstrings, $S=\{x_1, \ldots, x_n\}$, from $X$.  For each unique $x_i\in S$,
    set `states[i]` to the TFQ string representation of $U_\phi\ket{x_i}$, where
    $U_\phi$ is set by `self.modular_hamiltonian.circuit`.  Finally, set
    `counts[i]` equal to the number of times $x_i$ occurs in $S$.

    Args:
      model: The modular Hamiltonian whose normalized exponential is the
        density operator governing the ensemble of states from which to sample.
      num_samples: Number of states to draw from the ensemble.

    Returns:
      states: 1D `tf.Tensor` of dtype `tf.string`.  Each entry is a TFQ string
        representation of an eigenstate of `self.modular_hamiltonian`.
      counts: 1D `tf.Tensor` of dtype `tf.int32`.  `counts[i]` is the number of
        times `states[i]` was drawn from the ensemble.
    """
    samples = self.ebm.sample(num_samples)
    bitstrings, _, counts = utils.unique_bitstrings_with_counts(samples)
    states = self.modular_hamiltonian.circuit(bitstrings)
    return states, counts

  def expectation(self, observables: Union[tf.Tensor, hamiltonian.Hamiltonian]):
    """Estimates observable expectation values against the density operator.

    TODO(#119): add expectation and derivative equations and discussions
                from updated paper.

    Implicitly sample `num_samples` pure states from the canonical ensemble
    corresponding to the thermal state defined by `self.modular_hamiltonian`.  For each
    such state |psi>, estimate the expectation value <psi|op_j|psi> for each
    `ops[j]`. Then, average these expectation values over the sampled states.

    Args:
      model: The modular Hamiltonian whose normalized exponential is the
        density operator against which expectation values will be estimated.
      obervables: Hermitian operators to measure.  See docstring of
        `QuantumInference.expectation` for details.

    Returns:
      `tf.Tensor` with shape [n_ops] whose entries are are the sample averaged
      expectation values of each entry in `ops`.
    """
    return self.ebm.expectation(
        functools.partial(self.qnn.expectation, observables=observables))
