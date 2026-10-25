DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ledgerline') THEN
    CREATE ROLE ledgerline LOGIN PASSWORD 'ledgerline_pw';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'oncallify') THEN
    CREATE ROLE oncallify LOGIN PASSWORD 'oncallify_pw';
  END IF;
END
$$;

SELECT 'CREATE DATABASE ledgerline OWNER ledgerline'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'ledgerline')\gexec

SELECT 'CREATE DATABASE oncallify OWNER oncallify'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'oncallify')\gexec
