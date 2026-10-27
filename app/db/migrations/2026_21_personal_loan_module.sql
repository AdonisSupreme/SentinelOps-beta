-- Register only; administrators explicitly assign this module to sections.
BEGIN;
INSERT INTO access_modules(module_key,name,description,page_key)
VALUES ('reports.personal_loans','Personal loans — 48 months','Personal loan queue monitoring and guarded failed-record recovery','Reporting')
ON CONFLICT (module_key) DO NOTHING;
COMMIT;
