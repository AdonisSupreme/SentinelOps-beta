"""Pattern query failures must not be reported as an empty catalogue."""
import unittest
from unittest.mock import MagicMock, patch
from uuid import UUID
from app.services.shift_scheduling_service import ShiftSchedulingService

class PatternListingTests(unittest.TestCase):
    @patch('app.services.shift_scheduling_service.get_connection')
    def test_database_failure_propagates_for_retry(self, connect):
        connect.side_effect = RuntimeError('test database unavailable')
        with self.assertRaisesRegex(RuntimeError, 'test database unavailable'):
            ShiftSchedulingService.get_available_patterns(UUID(int=1))

    @patch('app.services.shift_scheduling_service.get_connection')
    def test_section_query_preserves_existing_patterns(self, connect):
        cursor = MagicMock()
        connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchall.return_value = [(UUID(int=2), 'Weekdays', '', 'FIXED', {}, None)]
        result = ShiftSchedulingService.get_available_patterns(UUID(int=1))
        self.assertEqual(result[0]['name'], 'Weekdays')
        self.assertEqual(cursor.execute.call_args.args[1], (str(UUID(int=1)),))
        self.assertIn('WHERE section_id = %s', cursor.execute.call_args.args[0])

if __name__ == '__main__':
    unittest.main()
