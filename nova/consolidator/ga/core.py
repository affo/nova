import random, operator
from oslo_log import log as logging
from oslo_config import cfg
from oslo_utils import importutils
from nova.consolidator.ga import k

ga_consolidator_opts = [
    cfg.FloatOpt(
        'prob_mutation',
        default=0.8,
        help='The probability to apply mutation'
    ),
    cfg.IntOpt(
        'mutation_perc',
        default=10,
        help='The percentage of genes to mutate'
    ),
    cfg.StrOpt(
        'selection_algorithm',
        default='nova.consolidator.ga.functions.RouletteSelection',
        help='The selection algorithm used'
    ),
    cfg.StrOpt(
        'fitness_function',
        default='nova.consolidator.ga.functions.NoNodesFitnessFunction',
        help='The fitness function used'
    ),
    cfg.IntOpt(
        'population_size',
        default=500,
        help='The size of population'
    ),
    cfg.IntOpt(
        'epoch_limit',
        default=100,
        help='The maximum number of epochs run in the algorithm'
    ),
    cfg.IntOpt(
        'elitism_perc',
        default=0,
        help='The percentage of the population that will become an elite'
    ),
    cfg.BoolOpt(
        'best',
        default=False,
        help='If True, mutation will choose directly the best host and only 1 epoch will be run'
    ),
]

CONF = cfg.CONF
cons_group = 'consolidator'
CONF.import_group(cons_group, 'nova.consolidator.base')
CONF.register_opts(ga_consolidator_opts, cons_group)

LOG = logging.getLogger(__name__)

# >>> import timeit
# >>> timeit.timeit('tuple(i for i in xrange(1000))')
# 52.415825843811035
# >>> timeit.timeit('[i for i in xrange(1000)]')
# 31.806253910064697
# >>> timeit.timeit('{i: i for i in xrange(1000)}')
# 53.94730615615845
#
# - we use LISTS
# - the cromosome is a LIST containing hostnames
# - each element is in the same position as in snapshot.instances_migrable
# - we don't repair, we don't make crossover:
#   the probability to get a valid child is near 0.
#   Crossover is replaced by a big (valid) mutation

def _call_with_prob(p, method, *args, **kwargs):
    if random.random() > p:
        # do nothing
        return None
    return method(*args, **kwargs)

class GA(object):
    BEST = CONF.consolidator.best
    LIMIT = CONF.consolidator.epoch_limit if not BEST else 1
    POP_SIZE = CONF.consolidator.population_size
    MUTATION_PERC = CONF.consolidator.mutation_perc
    MUTATION_PROB = CONF.consolidator.prob_mutation
    ELITISM_PERC = CONF.consolidator.elitism_perc

    def __init__(self, snapshot):
        super(GA, self).__init__()
        self._instances = snapshot.instances_migrable
        self._no_instances = len(self._instances)

        assert len(snapshot.nodes) > 0, 'Cannot init GA. No nodes given.'
        assert  self._no_instances > 0, 'Cannot init GA. No migrable instance.'

        # create hosts dict.
        # we create it only the first time at init.
        # nodes should be a few compared to instances
        def get_base_cap_tuple(node):
            locked_instances = node.instances_not_migrable

            if len(locked_instances) == 0:
                base_tuple = (0, 0, 0)
            else:
                base_list = [(i.vcpus, i.memory_mb, i.root_gb) for i in locked_instances]
                base_tuple = tuple(sum(m) for m in zip(*base_list))

            cap_metrics = (node.vcpus, node.memory_mb, node.local_gb)

            return (base_tuple, cap_metrics)

        self._hosts = {node.host: get_base_cap_tuple(node) for node in snapshot.nodes}
        self._flavors = [(i.vcpus, i.memory_mb, i.root_gb) for i in self._instances]
        self._indexes = list(range(self._no_instances))

        # init functions
        self.selection_algorithm = importutils.import_class(CONF.consolidator.selection_algorithm)()
        self.fitness_function = importutils.import_class(CONF.consolidator.fitness_function)()
        self.elite_len = int((float(self.ELITISM_PERC) / 100) * self.POP_SIZE)
        self.no_genes_mutate = int((float(self.MUTATION_PERC) / 100) * self._no_instances)

        self._max_fit = 1
        LOG.debug('Max fitness set to {}'.format(self._max_fit))
        if self.BEST:
            LOG.debug('GA set to BEST, will run only {} epoch'.format(self.LIMIT))
        else:
            LOG.debug('GA set NOT to BEST, will run {} epochs'.format(self.LIMIT))

        base_chromosome = [i.host for i in self._instances]
        self._starting_fitness = self._get_fitness(base_chromosome)
        LOG.debug('Base fitness is {}'.format(self._starting_fitness))
        # init population
        self.population = self._get_init_pop()

    def run(self):
        '''
            :returns: a couple comprising:
                - a dict {instance_id: hostname}
                - a boolean -> apply migrations or not
                    (the situation has improved from the beginning)
        '''
        count = 0
        _log_str = 'Epoch {}: best individual fitness is {}'
        def get_best_fit():
            return self._get_fitness(self.population[0])

        stop = self._stop()
        while count < self.LIMIT and not stop:
            self.population = self._next()
            self.population.sort(key=lambda ch: self._get_fitness(ch), reverse=True)
            if count % 10 == 0:
                best_fit = get_best_fit()
                LOG.debug(_log_str.format(count, best_fit))

            stop = self._stop()
            if stop:
                LOG.debug('Epoch {}: max fitness of {} exceeded, stopping...'.format(count, self._max_fit))

            count += 1
            
        best_fit = get_best_fit()
        LOG.debug(_log_str.format(count, best_fit))

        return {inst.id: self.population[0][i] for i, inst in enumerate(self._instances)}, best_fit > self._starting_fitness
 
    def _next(self):
        chromosomes_left = self.POP_SIZE - self.elite_len

        def new_chromosome():
            chosen = self.selection_algorithm.get_chromosome(self.population, self._get_fitness)
            mutated = _call_with_prob(self.MUTATION_PROB, self._mutate, chosen)
            if mutated is None: mutated = list(chosen) # not called
            return mutated

        new_pop = self._get_elite(self.population)
        new_pop.extend([new_chromosome() for i in xrange(chromosomes_left)])

        return new_pop

    def _stop(self):
        # ordered population
        return self._get_fitness(self.population[0]) >= self._max_fit

    def _get_init_pop(self):
        ini_pop = [self._rnd_chromosome() for i in xrange(self.POP_SIZE)]
        ini_pop.sort(key=lambda ch: self._get_fitness(ch), reverse=True)
        return ini_pop

    def _get_status(self, chromosome, hostname):
        indexes = filter(lambda i: chromosome[i] == hostname, self._indexes)
        metrics = [self._flavors[i] for i in indexes]
        # add the base to future sum
        metrics.append(k.get_base(self._hosts, hostname))
        return tuple(sum(m) for m in zip(*metrics))

    def _get_ratios(self, chromosome):
        def extract_ratio(hostname):
            status_tuple = self._get_status(chromosome, hostname)

            status_vcpus = k.get_vcpus(status_tuple) 
            status_ram = k.get_ram(status_tuple) 
            status_disk = k.get_disk(status_tuple) 

            cap_vcpus = k.get_vcpus(k.get_cap(self._hosts, hostname))
            cap_ram = k.get_ram(k.get_cap(self._hosts, hostname))
            cap_disk = k.get_disk(k.get_cap(self._hosts, hostname))

            return (
                float(status_vcpus) / cap_vcpus,
                float(status_ram) / cap_ram,
                float(status_disk) / cap_disk
            )

        return [extract_ratio(hostname) for hostname in self._hosts.keys()]

    def _validate_chromosome(self, chromosome):
        return not any(r > 1 for r in self._get_ratios(chromosome))

    def _extract_residual(self, hostname, instance_index, status):
        cap = k.get_cap(self._hosts, hostname)
        actual = status[hostname]
        flavor = self._flavors[instance_index]
        return (
            k.get_vcpus(cap) - k.get_vcpus(actual) - k.get_vcpus(flavor),
            k.get_ram(cap) - k.get_ram(actual) - k.get_ram(flavor),
            k.get_disk(cap) - k.get_disk(actual) - k.get_disk(flavor)
        )

    def _get_suitable_hostnames(self, instance_index, status):
        def host_ok(hostname):
            return all(residual >= 0 for residual in self._extract_residual(hostname, instance_index, status))

        return filter(host_ok, self._hosts.keys())

    def _get_best_suitable_hostname(self, instance_index, status, avoid=None):
        get_residual = lambda hostname: self._extract_residual(hostname, instance_index, status)
        residual_sums = {
            hostname: sum(get_residual(hostname))
            for hostname in status.keys()
            if all(r > 0 for r in get_residual(hostname)) # filtering
        }

        if avoid is not None: del residual_sums[avoid]
        return min(residual_sums.keys(), key=residual_sums.get)

    def _add_to_host(self, instance_index, status, avoid=None):
        # ! side effect on status
        hostname = None
        if self.BEST:
            hostname = self._get_best_suitable_hostname(instance_index, status, avoid=avoid)
        else:
            hostnames = self._get_suitable_hostnames(instance_index, status)
            if avoid is not None: hostnames.remove(avoid)
            hostname = random.choice(hostnames)

        status[hostname] = (
            k.get_vcpus(status[hostname]) + k.get_vcpus(self._flavors[instance_index]),
            k.get_ram(status[hostname]) + k.get_ram(self._flavors[instance_index]),
            k.get_disk(status[hostname]) + k.get_disk(self._flavors[instance_index])
        )
        return hostname

    def _remove_from_host(self, instance_index, status, hostname):
        # ! side effect on status
        status[hostname] = (
            k.get_vcpus(status[hostname]) - k.get_vcpus(self._flavors[instance_index]),
            k.get_ram(status[hostname]) - k.get_ram(self._flavors[instance_index]),
            k.get_disk(status[hostname]) - k.get_disk(self._flavors[instance_index])
        )
        return hostname

    def _rnd_chromosome(self):
        status = {h: k.get_base(self._hosts, h) for h in self._hosts.keys()}
        return [self._add_to_host(i, status) for i in xrange(self._no_instances)]

    def _mutate(self, chromosome):
        status = {h: self._get_status(chromosome, h) for h in self._hosts.keys()}
        to_mutate = random.sample(self._indexes, self.no_genes_mutate)

        def move_to_suitable_host(i):
            self._remove_from_host(i, status, chromosome[i])
            hostname = self._add_to_host(i, status, avoid=chromosome[i])
            return hostname

        return [
            move_to_suitable_host(i)
            if i in to_mutate
            else chromosome[i]
            for i in self._indexes
        ]

    def _get_fitness(self, chromosome):
        return self.fitness_function.get(chromosome)

    def _get_elite(self, pop):
        # expect pop is sorted by fitness
        return pop[:self.elite_len]
