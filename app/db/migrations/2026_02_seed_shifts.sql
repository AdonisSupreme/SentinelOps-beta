-- Seed default shifts for shift scheduling (Morning, Afternoon, Night)
DO $$
BEGIN
  IF (SELECT COUNT(*) FROM shifts) = 0 THEN
    INSERT INTO shifts (name, start_time, end_time, timezone, color) VALUES
      ('Morning', '07:00', '15:00', 'UTC', '#00f2ff'),
      ('Afternoon', '15:00', '23:00', 'UTC', '#00ff88'),
      ('Night', '23:00', '07:00', 'UTC', '#7c3aed');
  END IF;
END $$;
