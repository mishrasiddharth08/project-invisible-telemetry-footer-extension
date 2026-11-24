"""Offline regression checks: synthetic data only, no Probe initialization/GPU IO."""
import importlib.util
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


poller_module = load('pi_test_poller', 'lib/poller.py')
probe_module = load('pi_test_probe', 'lib/probe.py')


class FakeProbe:
    gpu_static = []
    driver = ''
    vram_backend = 'none'
    calls = 0

    def profile_intervals(self):
        return 500, 2500

    def snapshot(self):
        self.calls += 1
        return {'sequence': self.calls}


class TelemetryTests(unittest.TestCase):
    def test_driver_failure_and_missing_middle_card(self):
        p = probe_module.Probe.__new__(probe_module.Probe)
        p.handles = [0]
        p.gpu_static = [{'index':i, 'total_gb':8} for i in range(3)]
        p.nvml = SimpleNamespace()  # memory query unavailable
        self.assertEqual(p.gpu_memory(), [(None, 8)])
        p.nvml = None
        p.vram_backend = 'smi'
        p._smi_memory_cache = (-100, [])
        with patch.object(probe_module, 'smi_query', return_value='2,2048,8192\n1,N/A,8192\n0,0,8192'):
            self.assertEqual(p.gpu_memory(), [(0,8), (None,8), (2,8)])

    def test_expired_failure_and_disabled_snapshot(self):
        probe, poller = self.poller()
        poller._cache = {'sequence':1}
        with patch.object(probe, 'snapshot', side_effect=RuntimeError('offline')):
            with self.assertRaises(RuntimeError):
                poller.cached(max_age=-1)
            poller.opts_getter = lambda: {'enabled':False}
            self.assertFalse(poller.snapshot_with_cfg()['cfg']['enabled'])

    def test_monitor_fallback_without_duplicate_power(self):
        import json
        cpu = {'SensorType':'Power','Parent':'/intelcpu/0','Name':'CPU Package','Value':42}
        ram = {'SensorType':'Temperature','Parent':'/ram/0','Name':'DIMM','Value':36}
        result = SimpleNamespace(returncode=0, stdout=json.dumps([cpu])+'\n'+json.dumps([cpu,ram]))
        with patch.object(probe_module.subprocess, 'run', return_value=result):
            self.assertEqual(probe_module.TemperatureReader()._query(), {'cpu_power_w':42,'ram':36})

    def test_temperature_mapping_excludes_gpu_memory_and_bad_values(self):
        rows = [
            {'Parent':'/intelcpu/0', 'Name':'CPU Package', 'Value':65},
            {'Parent':'/intelcpu/0', 'Name':'CPU Core #1', 'Value':70},
            {'Parent':'/lpc/0', 'Name':'DIMM A2', 'Value':42},
            {'Parent':'/lpc/0', 'Name':'DIMM B2', 'Value':45},
            {'Parent':'/nvidiagpu/0', 'Name':'Memory', 'Value':95},
            {'Parent':'/ram/0', 'Name':'Memory', 'Value':None},
            {'Parent':'/intelcpu/0', 'Name':'CPU Package', 'Value':'NaN'},
            {'Parent':'/lpc/0', 'Name':'System', 'Value':30},
        ]
        self.assertEqual(probe_module.TemperatureReader.select(rows), {'cpu':65, 'ram':45})
        self.assertEqual(probe_module.TemperatureReader.select([]), {'cpu':None, 'ram':None})

    def test_temperature_cache_expires_and_errors_clear_readings(self):
        reader = probe_module.TemperatureReader()
        reader.values = {'cpu':65, 'ram':45}
        reader.sampled_at = 100
        reader.next_read = 200
        with patch.object(probe_module.platform, 'system', return_value='Windows'):
            with patch.object(probe_module.time, 'monotonic', return_value=104):
                self.assertEqual(reader.read()['cpu'], 65)
            with patch.object(probe_module.time, 'monotonic', return_value=106):
                self.assertEqual(reader.read(), {})
        with patch.object(reader, '_query', side_effect=RuntimeError('missing provider')):
            with patch.object(probe_module.time, 'monotonic', return_value=110):
                reader._update()
        self.assertEqual(reader.values, {})
        self.assertEqual(reader.next_read, 140)

    def test_gpu_temperature_is_read_only_and_optional(self):
        probe = probe_module.Probe.__new__(probe_module.Probe)
        probe.handles = ['fake']
        probe.nvml = SimpleNamespace(NVML_TEMPERATURE_GPU=0,
            nvmlDeviceGetTemperature=lambda handle, sensor: 58)
        self.assertEqual(probe.gpu_temperature(0), 58)
        probe.nvml.nvmlDeviceGetTemperature = lambda *args: None
        self.assertIsNone(probe.gpu_temperature(0))
        probe.nvml = None
        self.assertIsNone(probe.gpu_temperature(0))

    def test_power_mapping_and_nvml_units(self):
        rows = [
            {'SensorType':'Power','Parent':'/intelcpu/0','Name':'CPU Package','Value':75},
            {'SensorType':'Power','Parent':'/intelcpu/0','Name':'CPU Cores','Value':60},
            {'SensorType':'Power','Parent':'/intelcpu/0','Name':'CPU DRAM','Value':8},
            {'SensorType':'Power','Parent':'/nvidiagpu/0','Name':'Memory','Value':40},
            {'SensorType':'Power','Parent':'/ram/0','Name':'DIMM A','Value':4},
            {'SensorType':'Power','Parent':'/ram/1','Name':'DIMM B','Value':5},
            {'SensorType':'Temperature','Parent':'/intelcpu/0','Name':'CPU Package','Value':65},
        ]
        self.assertEqual(probe_module.TemperatureReader.select_power(rows),
                         {'cpu_power_w':75, 'ram_power_w':8})
        self.assertEqual(probe_module.TemperatureReader.select_power(rows[3:]),
                         {'cpu_power_w':None, 'ram_power_w':9})
        self.assertEqual(probe_module.TemperatureReader.select(rows)['cpu'],65)
        probe = probe_module.Probe.__new__(probe_module.Probe)
        probe.handles = ['fake']
        probe.nvml = SimpleNamespace(nvmlDeviceGetPowerUsage=lambda handle: 420500)
        self.assertEqual(probe.gpu_power(0),420.5)
        probe.nvml.nvmlDeviceGetPowerUsage = lambda handle: None
        self.assertIsNone(probe.gpu_power(0))
        for value in (None, True, -1, float('nan'), float('inf')):
            self.assertIsNone(probe_module.power_value(value))

    def test_vram_profiles_and_mixed_cards(self):
        probe = probe_module.Probe.__new__(probe_module.Probe)
        for capacity in (8, 10, 12, 16, 24, 32):
            with self.subTest(vram=capacity):
                probe.gpu_static = [{'total_gb':capacity}]
                self.assertEqual(probe.profile_intervals(),
                                 (750 if capacity <= 12 else 500, 2500))
        probe.gpu_static = [{'total_gb':32}, {'total_gb':8}]
        self.assertEqual(probe.profile_intervals(), (750, 2500))
        probe.gpu_static = []
        self.assertEqual(probe.profile_intervals(), (1000, 2500))

    def poller(self, values=None):
        probe = FakeProbe()
        # Avoid hardware fingerprinting or config writes entirely.
        with patch.object(poller_module.Poller, 'ensure_config', return_value=False):
            poller = poller_module.Poller(probe=probe, opts_getter=lambda: values or {})
        poller.read_config = lambda: {}
        return probe, poller

    def test_sampling_floor_preserves_requested_settings(self):
        _, poller = self.poller({'poll_idle_ms': 1, 'poll_busy_ms': 1})
        cfg = poller.effective_cfg()
        self.assertEqual((cfg['idle_ms'], cfg['busy_ms'], cfg['sample_ms']), (250, 250, 250))
        self.assertEqual((cfg['requested_idle_ms'], cfg['requested_busy_ms']), (1, 1))
        self.assertEqual(poller._next_interval(), 0.25)
        _, auto = self.poller()
        self.assertEqual(auto.effective_cfg()['idle_ms'], 2500)

    def test_concurrent_requests_share_one_sample(self):
        probe, poller = self.poller({'poll_idle_ms': 1})
        with patch.object(poller_module.time, 'monotonic', return_value=100.0):
            with ThreadPoolExecutor(max_workers=4) as executor:
                rows = list(executor.map(lambda _: poller.snapshot_with_cfg(), range(16)))
        self.assertEqual(probe.calls, 1)
        self.assertEqual({r['sequence'] for r in rows}, {1})
        with patch.object(poller_module.time, 'monotonic', return_value=100.249):
            poller.refresh()
        self.assertEqual(probe.calls, 1)
        with patch.object(poller_module.time, 'monotonic', return_value=100.251):
            self.assertEqual(poller.refresh()['sequence'], 2)

    def test_temperature_thresholds_are_independent_and_ordered(self):
        _, poller = self.poller({'temp_cpu_warn': 200, 'temp_cpu_crit': 20,
                                'temp_ram_warn': -50, 'temp_ram_crit': -40})
        cfg = poller.effective_cfg()
        self.assertEqual((cfg['temp_cpu_warn'], cfg['temp_cpu_crit']), (149, 150))
        self.assertEqual((cfg['temp_ram_warn'], cfg['temp_ram_crit']), (-20, -19))
        self.assertEqual((cfg['temp_gpu_warn'], cfg['temp_gpu_crit']), (80, 90))

    def test_cpu_clock_and_load_share_a_sampling_cycle(self):
        times = namedtuple('Times', 'user system idle')
        state = {'times': times(30, 10, 60)}
        clock_calls = []
        probe = probe_module.Probe.__new__(probe_module.Probe)
        probe.lock = threading.RLock()
        probe.psutil = SimpleNamespace(
            cpu_times=lambda: state['times'],
            virtual_memory=lambda: SimpleNamespace(total=100, available=60, percent=40),
            swap_memory=lambda: SimpleNamespace(used=0, total=0, percent=0))
        probe._cpu_times_prev = (0.0, 0.0)
        probe._cpu_cache = (0.0, 0.0)
        probe._cpu_mhz_now = probe.cpu_mhz = 3400
        probe.clock = SimpleNamespace(read=lambda force=False: clock_calls.append(force) or 4200)
        probe.cpu_name = 'Fake CPU'
        probe.cores_logical = probe.cores_physical = 1
        probe.gpu_static = []
        probe.ram_hw = {}
        probe.gpu_memory = lambda: []
        probe.disk_usage = lambda: {}
        with patch.object(probe_module.time, 'monotonic', return_value=10.0):
            first = probe.snapshot()
        self.assertEqual(first['cpu']['pct'], 40.0)
        self.assertEqual(first['cpu']['mhz_now'], 4200)
        with patch.object(probe_module.time, 'monotonic', return_value=10.1):
            self.assertEqual(probe.snapshot()['cpu'], first['cpu'])
        self.assertEqual(clock_calls, [True])
        state['times'] = times(60, 20, 120)
        with patch.object(probe_module.time, 'monotonic', return_value=10.25):
            probe.snapshot()
        self.assertEqual(clock_calls, [True, True])
        probe.psutil = None
        self.assertEqual(probe.snapshot()['cpu']['mhz_now'], 4200)


if __name__ == '__main__':
    unittest.main()
