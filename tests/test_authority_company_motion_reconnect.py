"""No X4. Exercise finite reconnect work and fail-closed movement errors."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from authority import company_motion_reconnect as subject


class MotionReconnectTests(unittest.TestCase):
    def make(self):
        source = SimpleNamespace(poll=Mock(return_value=None), registry=('b' * 32, 7),
                                 local_generation='a' * 32)
        credential = {'ticket': 'f' * 64}
        return subject.ReconnectingCompanyMotionClient(1, credential, source, Mock()), source

    def test_constructor_refusal_retries_at_capped_intervals_without_spinning(self):
        client, _ = self.make()
        time = 0.0
        with patch.object(subject.time, 'monotonic', side_effect=lambda: time), \
                patch.object(subject, 'CompanyMotionClient', side_effect=ConnectionRefusedError) as connect:
            connect.assert_not_called()
            attempts = 0
            for time in (0, .25, .75, 1.75, 3.75, 7.75, 12.75, 17.75, 22.75, 27.75):
                client.poll()
                attempts += 1
                self.assertEqual(connect.call_count, attempts)
                for _ in range(10):
                    client.poll()
                self.assertEqual(connect.call_count, attempts)
            self.assertIsNone(client.failure)
            client.close()
            time += 60
            client.poll()
            self.assertEqual(connect.call_count, attempts)
            self.assertIsNone(client._credential)

    def test_invalid_protocol_disables_motion_without_affecting_caller(self):
        client, _ = self.make()
        link = Mock()
        counts = dict(samples_acknowledged=3, velocity_samples_acknowledged=2,
                      snapshots_received=1, visibility_ends=1)
        link.stats.return_value = counts
        link.poll.side_effect = ValueError('untrusted private detail')
        with patch.object(subject, 'CompanyMotionClient', return_value=link) as connect:
            client.poll()
            self.assertEqual(client.failure, 'motion_local_or_protocol_failure')
            client.poll()
            connect.assert_called_once()
            link.close.assert_called_once()
            self.assertEqual(client.stats(), counts)
            client.close()
            link.close.assert_called_once()

    def test_local_socket_error_does_not_schedule_a_transport_retry(self):
        client, source = self.make()
        source.poll.side_effect = OSError('owned source closed')
        with patch.object(subject, 'CompanyMotionClient') as connect:
            client.poll()
            client.poll()
            source.poll.assert_called_once()
            connect.assert_not_called()
        self.assertEqual(client.failure, 'motion_local_or_protocol_failure')
        self.assertIsNone(client._credential)

    def test_reports_the_confirmed_motion_window_and_invalidates_on_link_loss(self):
        source = SimpleNamespace(poll=Mock(return_value=None), registry=('b' * 32, 7), local_generation='a' * 32)
        on_clock = Mock()
        client = subject.ReconnectingCompanyMotionClient(1, {'ticket': 'f' * 64}, source, Mock(), on_clock=on_clock)
        link = Mock()
        link.stats.return_value = dict.fromkeys(subject._COUNTERS, 0)
        with patch.object(subject, 'CompanyMotionClient', return_value=link) as connect:
            client.poll()
            current = connect.call_args.kwargs['on_clock']
            current(1, True)
            current(2, False)
            current(3, True)
            link.poll.side_effect = ConnectionResetError
            client.poll()
        self.assertEqual(on_clock.call_args_list, [call(1, True), call(2, False), call(3, True), call(None, False)])


if __name__ == '__main__':
    unittest.main()
