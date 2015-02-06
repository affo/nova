"""
Unit Tests for nova.consolidator.base
"""

import contextlib

import mock
from oslo_config import cfg
from nova import context
from nova import test
from nova.consolidator import base
from nova.tests.unit import fake_instance

CONF = cfg.CONF

class BaseConsolidatorTestCase(test.TestCase):

	def setUp(self):
		super(BaseConsolidatorTestCase, self).setUp()
		self.context = context.get_admin_context()
		self.consolidator = base.BaseConsolidator()
		self.Migration = self.consolidator.Migration
		
		self.host_names = ['hostA', 'hostB', 'hostC', 'hostD']
		self.fake_instances = [
			self._create_fake_instance(0, self.host_names[0]),
			self._create_fake_instance(1, self.host_names[0]),
			self._create_fake_instance(2, self.host_names[1]),
			self._create_fake_instance(3, self.host_names[1]),
			self._create_fake_instance(4, self.host_names[1]),
			self._create_fake_instance(5, self.host_names[2]),
			self._create_fake_instance(6, self.host_names[3]),
		]
		self.fake_migrations_ok = [
			self.Migration(self.fake_instances[0], self.host_names[1]),
			self.Migration(self.fake_instances[2], self.host_names[2]),
			self.Migration(self.fake_instances[3], self.host_names[0]),
			self.Migration(self.fake_instances[5], self.host_names[0]),
		]

		self.fake_migrations_ko = [
			self.Migration(self.fake_instances[0], self.host_names[1]), # expected to disappear
			self.Migration(self.fake_instances[2], self.host_names[2]), 
			self.Migration(self.fake_instances[3], self.host_names[0]), # expected to disappear
			self.Migration(self.fake_instances[5], self.host_names[0]),
			self.Migration(self.fake_instances[0], self.host_names[2]),
			self.Migration(self.fake_instances[3], self.host_names[3]),
			self.Migration(self.fake_instances[1], self.host_names[3]), # expected to disappear
			self.Migration(self.fake_instances[1], self.host_names[2])
		]

	def _create_fake_instance(self, id, host_name):
		instance_attr = {'host': host_name, 'id': id}
		return fake_instance.fake_instance_obj(self.context, **instance_attr)

	def test_consolidate_with_closed_migrations(self):
		self.consolidator.get_migrations = mock.Mock(return_value=self.fake_migrations_ok)
		res = self.consolidator.consolidate()
		self.assertSequenceEqual(res, self.fake_migrations_ok)

	def test_consolidate_with_not_closed_migrations(self):
		self.consolidator.get_migrations = mock.Mock(return_value=self.fake_migrations_ko)
		expected = list(self.fake_migrations_ko) #copy
		del expected[0]
		del expected[1]
		del expected[4]
		real = self.consolidator.consolidate()
		self.assertItemsEqual(expected, real)