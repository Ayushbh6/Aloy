-- Existing installs may persist the v1 tool list. Enable canvas once for those installs;
-- subsequent explicit changes are preserved because this migration is recorded as applied.
UPDATE settings
SET value=json_insert(value, '$[#]', 'canvas.present')
WHERE key='tools'
  AND json_valid(value)
  AND json_type(value)='array'
  AND NOT EXISTS (
    SELECT 1 FROM json_each(settings.value)
    WHERE json_each.type='text' AND json_each.value='canvas.present'
  );
