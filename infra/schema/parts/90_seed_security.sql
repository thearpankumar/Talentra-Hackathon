-- 32 audit hash chains (BR-ADM-09): one head row per chain, created empty
INSERT INTO audit_chain_head (created_by, updated_by, chain_no)
SELECT '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', g::INT2
FROM generate_series(0, 31) AS g ON CONFLICT (chain_no) DO NOTHING;
