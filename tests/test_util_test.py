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
"""Tests for tests.test_util"""

from absl.testing import parameterized

import cirq
import sympy
import tensorflow as tf

from tests import test_util


class RPQCTest(tf.test.TestCase, parameterized.TestCase):
  """Test RPQC functions in the test_util module."""

  def test_get_xz_rotation(self):
    """Confirm an XZ rotation is returned."""
    q = cirq.GridQubit(7, 9)
    a, b = sympy.symbols("a b")
    expected_circuit = cirq.Circuit(cirq.X(q)**a, cirq.Z(q)**b)
    actual_circuit = test_util.get_xz_rotation(q, a, b)
    self.assertEqual(actual_circuit, expected_circuit)

  def test_get_cz_exp(self):
    """Confirm an exponentiated CNOT is returned."""
    q0 = cirq.GridQubit(4, 1)
    q1 = cirq.GridQubit(2, 5)
    a = sympy.Symbol("a")
    expected_circuit = cirq.Circuit(cirq.CZ(q0, q1)**a)
    actual_circuit = test_util.get_cz_exp(q0, q1, a)
    self.assertEqual(actual_circuit, expected_circuit)

  def test_get_xz_rotation_layer(self):
    """Confirm an XZ rotation on every qubit is returned."""
    qubits = cirq.GridQubit.rect(1, 2)
    layer_num = 3
    name = "test_rot"
    expected_circuit = cirq.Circuit()
    for n, q in enumerate(qubits):
      s = sympy.Symbol("sx_{0}_{1}_{2}".format(name, layer_num, n))
      expected_circuit += cirq.Circuit(cirq.X(q)**s)
      s = sympy.Symbol("sz_{0}_{1}_{2}".format(name, layer_num, n))
      expected_circuit += cirq.Circuit(cirq.Z(q)**s)
    actual_circuit = test_util.get_xz_rotation_layer(qubits, layer_num, name)
    self.assertEqual(actual_circuit, expected_circuit)

  @parameterized.parameters([{"n_qubits": 11}, {"n_qubits": 12}])
  def test_get_cz_exp_layer(self, n_qubits):
    """Confirm an exponentiated CZ on every qubit is returned."""
    qubits = cirq.GridQubit.rect(1, n_qubits)
    layer_num = 0
    name = "test_cz"
    expected_circuit = cirq.Circuit()
    for n, (q0, q1) in enumerate(zip(qubits, qubits[1:])):
      if n % 2 == 0:
        s = sympy.Symbol("sc_{0}_{1}_{2}".format(name, layer_num, n))
        expected_circuit += cirq.Circuit(cirq.CZ(q0, q1)**s)
    for n, (q0, q1) in enumerate(zip(qubits, qubits[1:])):
      if n % 2 == 1:
        s = sympy.Symbol("sc_{0}_{1}_{2}".format(name, layer_num, n))
        expected_circuit += cirq.Circuit(cirq.CZ(q0, q1)**s)
    actual_circuit = test_util.get_cz_exp_layer(qubits, layer_num, name)
    self.assertEqual(actual_circuit, expected_circuit)

  @parameterized.parameters([{"n_qubits": 11}, {"n_qubits": 12}])
  def test_get_hardware_efficient_model_unitary(self, n_qubits):
    """Confirm a multi-layered circuit is returned."""
    qubits = cirq.GridQubit.rect(1, n_qubits)
    name = "test_hardware_efficient_model"
    expected_circuit = cirq.Circuit()
    this_circuit = test_util.get_xz_rotation_layer(qubits, 0, name)
    expected_circuit += this_circuit
    this_circuit = test_util.get_cz_exp_layer(qubits, 0, name)
    expected_circuit += this_circuit
    this_circuit = test_util.get_xz_rotation_layer(qubits, 1, name)
    expected_circuit += this_circuit
    this_circuit = test_util.get_cz_exp_layer(qubits, 1, name)
    expected_circuit += this_circuit
    actual_circuit = test_util.get_hardware_efficient_model_unitary(
        qubits, 2, name)
    self.assertEqual(actual_circuit, expected_circuit)


class EagerModeToggleTest(tf.test.TestCase):
  """Tests eager_mode_toggle."""

  def test_eager_mode_toggle(self):
    """Ensure eager mode really gets toggled."""

    def fail_in_eager():
      """Raises AssertionError if run in eager."""
      if tf.config.functions_run_eagerly():
        raise AssertionError()

    def fail_out_of_eager():
      """Raises AssertionError if run outside of eager."""
      if not tf.config.functions_run_eagerly():
        raise AssertionError()

    with self.assertRaises(AssertionError):
      test_util.eager_mode_toggle(fail_in_eager)()

    # Ensure eager mode still turned off even though exception was raised.
    self.assertFalse(tf.config.functions_run_eagerly())

    with self.assertRaises(AssertionError):
      test_util.eager_mode_toggle(fail_out_of_eager)()


class PerturbFunctionTest(tf.test.TestCase, parameterized.TestCase):
  """Tests perturb_function."""

  @test_util.eager_mode_toggle
  def test_side_effects(self):
    """Checks that variable is perturbed and then returned to initial value."""
    initial_value = tf.constant([4.5, -1.3])
    basic_variable = tf.Variable(initial_value)

    def f():
      """Basic test function."""
      return basic_variable.read_value()

    test_delta = 0.5
    wrapped_perturb_function = tf.function(test_util.perturb_function)
    actual_return = wrapped_perturb_function(f, basic_variable, 1, test_delta)
    expected_return = initial_value + [0, test_delta]
    self.assertIsInstance(actual_return, tf.Tensor)
    self.assertAllClose(actual_return, expected_return)
    self.assertAllClose(basic_variable, initial_value)

  @parameterized.parameters([{"this_type": t} for t in [tf.float16, tf.float32, tf.float64, tf.complex64, tf.complex128]])
  def test_multi_variable(self, this_type):
    """Tests perturbation when there are multiple differently shaped vars."""
    dimension = 7
    minval = -5
    maxval = 5
    scalar_initial_value = tf.cast(tf.random.uniform([], minval, maxval), this_type)
    scalar_var = tf.Variable(scalar_initial_value)
    vector_initial_value = tf.cast(tf.random.uniform([dimension], minval, maxval), this_type)
    vector_var = tf.Variable(vector_initial_value)
    matrix_initial_value = tf.cast(tf.random.uniform([dimension, dimension], minval, maxval), this_type)
    matrix_var = tf.Variable(matrix_initial_value)

    def f():
      """Vector result of combining the variables."""
      val = tf.linalg.matvec(matrix_var, vector_var) * scalar_var
      return [val, [val, val]]

    test_delta = tf.cast(tf.random.uniform([]), this_type)
    test_delta_python = test_delta.numpy().tolist()
    wrapped_perturb_function = tf.function(test_util.perturb_function)

    # check scalar perturbation
    perturbed_scalar = scalar_var + test_delta
    expected_val = tf.linalg.matvec(matrix_var, vector_var) * perturbed_scalar
    expected_return = [expected_val, [expected_val, expected_val]]
    actual_return = wrapped_perturb_function(f, scalar_var, 0, test_delta)
    tf.nest.map_structure(lambda x: self.assertIsInstance(x, tf.Tensor), actual_return)
    tf.nest.map_structure(self.assertAllClose, actual_return, expected_return)
    self.assertAllClose(scalar_var, scalar_initial_value)

    # check vector perturbations
    for i in range(dimension):
      vector_list = vector_initial_value.numpy().tolist()
      perturbation_vector = [test_delta_python if j == i else 0 for j in range(dimension)]
      perturbed_vector_list = [v + v_p for v, v_p in zip(vector_list, perturbation_vector)]
      perturbed_vector = tf.constant(perturbed_vector_list, this_type)
      expected_val = tf.linalg.matvec(matrix_var, perturbed_vector) * scalar_var
      expected_return = [expected_val, [expected_val, expected_val]]
      actual_return = wrapped_perturb_function(f, vector_var, i, test_delta)
      tf.nest.map_structure(lambda x: self.assertIsInstance(x, tf.Tensor), actual_return)
      tf.nest.map_structure(self.assertAllClose, actual_return, expected_return)
      self.assertAllClose(vector_var, vector_initial_value)

    # check matrix perturbations
    for i in range(dimension * dimension):
      matrix_list = tf.reshape(matrix_initial_value, [dimension * dimension]).numpy().tolist()
      perturbation_matrix = [test_delta_python if j == i else 0 for j in range(dimension * dimension)]
      perturbed_matrix_list = [m + m_p for m, m_p in zip(matrix_list, perturbation_matrix)]
      perturbed_matrix = tf.reshape(tf.constant(perturbed_matrix_list, this_type), [dimension, dimension])
      expected_val = tf.linalg.matvec(perturbed_matrix, vector_var) * scalar_var
      expected_return = [expected_val, [expected_val, expected_val]]
      actual_return = wrapped_perturb_function(f, matrix_var, i, test_delta)
      tf.nest.map_structure(lambda x: self.assertIsInstance(x, tf.Tensor), actual_return)
      tf.nest.map_structure(self.assertAllClose, actual_return, expected_return)
      self.assertAllClose(matrix_var, matrix_initial_value)
