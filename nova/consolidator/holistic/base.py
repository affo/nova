from nova.consolidator.base import BaseConsolidator
from nova.consolidator.holistic.core import Holistic
from oslo_log import log as logging
from nova.i18n import _LI

LOG = logging.getLogger(__name__)

class HolisticConsolidator(BaseConsolidator):

  def _get_migrations_from_new_state(self, snapshot, new_state):
    h_mapping = {h.host: h for h in snapshot.nodes}

    return [
      self.Migration(inst, h_mapping[new_state[inst.id]])
      for inst in snapshot.instances_migrable
      if new_state[inst.id] != inst.host
    ]


  def get_migrations(self, snapshot):
    no_nodes = len(snapshot.nodes)
    no_inst = len(snapshot.instances_migrable)

    if no_inst == 0:
      LOG.info(_LI('No running instance found. Cannot migrate.'))
      return []

    if no_nodes == 0:
      LOG.info(_LI('No compute node in current snapshot'))
      return []

    if no_nodes == 1:
      LOG.info(_LI('Only one compute node in current snapshot. Cannot migrate.'))
      return []

    # ok, let's go
    holi = Holistic(snapshot)
    new_state, migrate = holi.run()
    migs = []
    if migrate:
      migs = self._get_migrations_from_new_state(snapshot, new_state)
    else:
      LOG.info(_LI('We cannot improve the situation, not migrating'))

    return migs