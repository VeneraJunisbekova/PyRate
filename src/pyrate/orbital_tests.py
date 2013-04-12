'''
Collection of tests for orbital correction.

Created on 31/3/13
@author: Ben Davies
'''


import unittest
from os.path import join

from numpy.linalg import pinv
from numpy import nan, isnan, array, reshape, float32
from numpy import empty, ones, zeros, meshgrid, dot
from numpy.testing import assert_array_equal, assert_array_almost_equal

import algorithm
from shared import Ifg
from orbital import OrbitalError, orbital_correction
from orbital import get_design_matrix, get_network_design_matrix
from orbital import INDEPENDENT_METHOD, NETWORK_METHOD, PLANAR, QUADRATIC
from tests_common import sydney5_mock_ifgs, MockIfg, IFMS5


# TODO: test lstsq() here against Hua's manual method

# ORDER of work:
# fix up offsets options for QUAD DM tests
# multilooking (do ML at start when multilooking the originals)
# high level functions/workflow


class IndependentDesignMatrixTests(unittest.TestCase):
	'''Tests for verifying various forms of design matricies'''

	def setUp(self):
		# fake cell sizes
		self.xs = 0.6
		self.ys = 0.7
		self.ifg = Ifg("../../tests/sydney_test/obs/geo_060619-061002.unw")
		self.ifg.open()


	def test_design_matrix_planar(self):
		m = MockIfg(self.ifg, 2, 3)
		m.X_SIZE = self.xs
		m.Y_SIZE = self.ys

		# test with and without offsets option
		exp = unittest_dm(m, INDEPENDENT_METHOD, PLANAR, True)
		assert_array_almost_equal(exp, get_design_matrix(m, PLANAR, True))
		assert_array_almost_equal(exp[:, :-1], get_design_matrix(m, PLANAR, False))


	def test_design_matrix_quadratic(self):
		m = MockIfg(self.ifg, 3, 5)
		m.X_SIZE = self.xs
		m.Y_SIZE = self.ys

		# use exp & subset of exp to test against both forms of DM
		exp = unittest_dm(m, INDEPENDENT_METHOD, QUADRATIC, True)
		design_mat = get_design_matrix(m, QUADRATIC, False) # no offset
		assert_array_almost_equal(exp[:, :-1], design_mat)
		assert_array_almost_equal(exp, get_design_matrix(m, QUADRATIC, True))


class OrbitalCorrection(unittest.TestCase):
	'''Test cases for the orbital correction component of PyRate.'''

	def test_ifg_to_vector(self):
		# test numpy reshaping order
		ifg = zeros((2, 3), dtype=float32)
		a = [24, 48, 1000]
		b = [552, 42, 68]
		ifg[0] = a
		ifg[1] = b
		res = reshape(ifg, (len(a+b)))
		exp = array(a + b)
		assert_array_equal(exp, res)

		# ensure order is maintained when converting back
		tmp = reshape(res, ifg.shape)
		assert_array_equal(tmp, ifg)


	def test_independent_correction(self):
		# verify top level orbital correction function
		# TODO: check correction with NaN rows

		def test_results():
			'''Helper method for result verification'''
			for i, c in zip(ifgs, corrections):
				# are corrections same size as the original array?
				ys, xs = c.shape
				self.assertEqual(i.FILE_LENGTH, ys)
				self.assertEqual(i.WIDTH, xs)
				self.assertFalse(isnan(i.phase_data).all())
				self.assertFalse(isnan(c).all())
				self.assertTrue(c.ptp() != 0) # ensure range of values in grid
				# TODO: do the results need to be checked at all?

		ifgs = sydney5_mock_ifgs()
		for ifg in ifgs:
			ifg.X_SIZE = 90.0
			ifg.Y_SIZE = 89.5
			ifg.open()

		ifgs[0].phase_data[1, 1:3] = nan # add some NODATA

		# test both models with no offsets
		for m in [PLANAR, QUADRATIC]:
			corrections = orbital_correction(ifgs, m, INDEPENDENT_METHOD, False)
			test_results()

		# test both with offsets
		for m in [PLANAR, QUADRATIC]:
			corrections = orbital_correction(ifgs, m, INDEPENDENT_METHOD)
			test_results()



class ErrorTests(unittest.TestCase):
	'''Tests for the networked correction method'''

	def test_invalid_ifgs_arg(self):
		# min requirement is 1 ifg, can still subtract one epoch from the other
		self.assertRaises(OrbitalError, get_network_design_matrix, [], PLANAR, True)


	def test_invalid_degree_arg(self):
		ifgs = sydney5_mock_ifgs()
		for d in range(-5, 1):
			self.assertRaises(OrbitalError, get_network_design_matrix, ifgs, d, True)
		for d in range(3, 6):
			self.assertRaises(OrbitalError, get_network_design_matrix, ifgs, d, True)


	def test_invalid_method(self):
		ifgs = sydney5_mock_ifgs()
		for m in [None, 5, -1, -3, 45.8]:
			self.assertRaises(OrbitalError, orbital_correction, ifgs, PLANAR, m, True)


	def test_multilooked_ifgs_arg(self):
		# check a variety of bad args for network method multilooked ifgs
		ifgs = sydney5_mock_ifgs()
		args = [[None, None, None, None, None], ["X"] * 5]
		for a in args:
			args = (ifgs, PLANAR, NETWORK_METHOD, a)
			self.assertRaises(OrbitalError, orbital_correction, *args)

		# ensure failure if uneven ifgs lengths
		args = (ifgs, PLANAR, NETWORK_METHOD, ifgs[:4])
		self.assertRaises(OrbitalError, orbital_correction, *args)



class NetworkDesignMatrixTests(unittest.TestCase):
	'''Contains tests verifying creation of sparse network design matrix.'''
	# TODO: add nodata to several layers
	# TODO: check correction with NaN rows

	def setUp(self):
		self.ifgs = sydney5_mock_ifgs()
		self.nifgs = len(self.ifgs)
		self.nc = self.ifgs[0].num_cells
		self.nepochs = self.nifgs + 1 # assumes MST done
		self.date_ids = get_date_ids(self.ifgs)

		for ifg in self.ifgs:
			ifg.X_SIZE = 90.0
			ifg.Y_SIZE = 89.5


	def test_network_design_matrix_planar(self):
		ncoef = 2
		deg = PLANAR
		offset = False
		act = get_network_design_matrix(self.ifgs, deg, offset)
		self.assertEqual(act.shape, (self.nc * self.nifgs, ncoef * self.nepochs))
		self.assertNotEqual(act.ptp(), 0)
		self.check_equality(ncoef, act, self.ifgs, offset)


	def test_network_design_matrix_planar_offset(self):
		ncoef = 2
		np = ncoef * self.nepochs
		deg = PLANAR
		offset = True
		act = get_network_design_matrix(self.ifgs, deg, offset)
		self.assertEqual(act.shape, (self.nc * self.nifgs, np + self.nifgs))
		self.assertNotEqual(act.ptp(), 0)
		self.check_equality(ncoef, act, self.ifgs, offset)


	def test_network_design_matrix_quad(self):
		ncoef = 5
		deg = QUADRATIC
		offset = False
		act = get_network_design_matrix(self.ifgs, deg, offset)
		self.assertEqual(act.shape, (self.nc * self.nifgs, ncoef * self.nepochs))
		self.assertNotEqual(act.ptp(), 0)
		self.check_equality(ncoef, act, self.ifgs, offset)


	def test_network_design_matrix_quad_offset(self):
		ncoef = 5
		deg = QUADRATIC
		offset = True
		act = get_network_design_matrix(self.ifgs, deg, offset)
		exp = (self.nc * self.nifgs, (ncoef * self.nepochs) + self.nifgs)
		self.assertEqual(act.shape, exp)
		self.assertNotEqual(act.ptp(), 0)
		self.check_equality(ncoef, act, self.ifgs, offset)


	def check_equality(self, ncoef, dm, ifgs, offset):
		'''Internal test function to check subsets against network design matrix.'''

		self.assertTrue(ncoef in [2,5])
		deg = PLANAR if ncoef < 5 else QUADRATIC
		np = ncoef * self.nepochs

		for i, ifg in enumerate(ifgs):
			exp = unittest_dm(ifg, NETWORK_METHOD, deg, offset)
			self.assertEqual(exp.shape, (ifg.num_cells, ncoef)) # subset DMs shouldn't have an offsets col

			# use slightly refactored version of Hua's code to test
			ib1 = i * self.nc # start row for subsetting the sparse matrix
			ib2 = (i+1) * self.nc # last row of subset of sparse matrix
			jbm = self.date_ids[ifg.MASTER] * ncoef # starting row index for master
			jbs = self.date_ids[ifg.SLAVE] * ncoef # row start for slave
			assert_array_almost_equal(-exp, dm[ib1:ib2, jbm:jbm+ncoef])
			assert_array_almost_equal(exp, dm[ib1:ib2, jbs:jbs+ncoef])

			# ensure rest of row is zero (in different segments)
			assert_array_equal(0, dm[ib1:ib2, :jbm])
			assert_array_equal(0, dm[ib1:ib2, jbm+ncoef:jbs])

			# check offset cols
			if offset is True:
				self.assertTrue(bool((dm[ib1:ib2, i+np] == 1).all()))
				assert_array_equal(0, dm[ib1:ib2, jbs+ncoef:i+np])
				assert_array_equal(0, dm[ib1:ib2, i+np+1:])
			else:
				assert_array_equal(0, dm[ib1:ib2, jbs+ncoef:])


	def test_network_correct_planar(self):
		'''Verifies planar form of network method of correction'''

		# add some nans to the data
		self.ifgs[0].phase_data[0, :] = nan # 3 error cells
		self.ifgs[1].phase_data[2, 1:3] = nan # 2 error cells
		self.ifgs[2].phase_data[3, 2:3] = nan # 1 err
		self.ifgs[3].phase_data[1, 2] = nan # 1 err
		self.ifgs[4].phase_data[1, 1:3] = nan # 2 err
		err = sum([i.nan_count for i in self.ifgs])

		# reshape phase data of all ifgs to single vector
		data = empty(self.nifgs * self.nc, dtype=float32)
		for i, ifg in enumerate(self.ifgs):
			st = i * self.nc
			data[st:st + self.nc] = ifg.phase_data.reshape(self.nc)

		# filter out NaN cells
		dm = get_network_design_matrix(self.ifgs, PLANAR, False)[~isnan(data)]
		ns = ( (self.nc * self.nifgs) - err, 1)
		fd = data[~isnan(data)].reshape(ns)
		self.assertEqual(len(dm), len(fd))

		ncoef = 2
		params = dot(pinv(dm, 1e-6), fd)
		self.assertEqual(params.shape[1], 1) # needs to be matrix multiplied/reduced

		# calculate forward correction
		sdm = unittest_dm(ifg, NETWORK_METHOD, PLANAR, offset=False)
		self.assertEqual(sdm.shape, (12,2))

		corrections = []
		for i, ifg in enumerate(self.ifgs):
			jbm = self.date_ids[ifg.MASTER] * ncoef # starting row index for master
			jbs = self.date_ids[ifg.SLAVE] * ncoef # row start for slave
			par = params[jbs:jbs + ncoef] - params[jbm:jbm + ncoef]
			corrections.append(dot(sdm, par).reshape(ifg.phase_data.shape))

		act = orbital_correction(self.ifgs, PLANAR, NETWORK_METHOD, offset=False)
		assert_array_almost_equal(act, corrections)

		# FIXME: expand test to include offsets


	def test_network_correct_quadratic(self):
		'''Verifies quadratic form of network method of correction'''

		self.ifgs[0].phase_data[3, 0:7] = nan # add NODATA as rasters are complete

		# reshape phase data to vectors
		data = empty(self.nifgs * self.nc, dtype=float32)
		for i, ifg in enumerate(self.ifgs):
			st = i * self.nc
			data[st:st + self.nc] = ifg.phase_data.reshape(self.nc)

		dm = get_network_design_matrix(self.ifgs, QUADRATIC, False)[~isnan(data)]
		params = pinv(dm, 1e-6) * data[~isnan(data)]

		# TODO: add fwd correction calc & testing here

		raise NotImplementedError("TODO: add forward correction implementation")

		act = orbital_correction(self.ifgs, QUADRATIC, NETWORK_METHOD, offset=False)
		assert_array_almost_equal(act, params, decimal=5) # TODO: fails occasionally on default decimal=6

		# FIXME: expand test to include offsets


def unittest_dm(ifg, method, degree, offset=False):
	'''Convenience function to create design matrices.
	xs - X axis cell size
	ys - Y axis cell size
	'''
	assert method in [INDEPENDENT_METHOD, NETWORK_METHOD]
	ncoef = 2 if degree == PLANAR else 5
	if offset is True and method == INDEPENDENT_METHOD:
		ncoef += 1

	out = ones((ifg.num_cells, ncoef), dtype=float32)
	x, y = meshgrid(range(ifg.WIDTH), range(ifg.FILE_LENGTH))
	x = x.reshape(ifg.num_cells) * ifg.X_SIZE
	y = y.reshape(ifg.num_cells) * ifg.Y_SIZE

	if degree == PLANAR:
		out[:, 0] = x
		out[:, 1] = y
	elif degree == QUADRATIC:
		out[:, 0] = x**2
		out[:, 1] = y**2
		out[:, 2] = x * y
		out[:, 3] = x
		out[:, 4] = y
	else:
		raise Exception("Degree is invalid")

	return out


def get_date_ids(ifgs):
	'''Returns unique master/slave date IDs from the given Ifgs'''
	dates = []
	for ifg in ifgs:
		dates += list(ifg.DATE12)
	return algorithm.master_slave_ids(dates)


if __name__ == "__main__":
	unittest.main()
