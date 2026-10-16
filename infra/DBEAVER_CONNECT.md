# Connecting with DBeaver (and psql) to the Talentra databases

Everything goes through the public domain names with full certificate verification (`sslmode=verify-full`). CockroachDB speaks the PostgreSQL protocol, so use DBeaver's **PostgreSQL** driver. No passwords are in this repository: the operator holds them in `.aws-creds/prod-credentials.txt` (gitignored, laptop only).

## 1. Get the public CA certificate (not secret)
The clusters use their own CA, so the client must trust it. Download the file from this repository (public certificate only, never a key):
- main cluster: `infra/certs/main-ca.crt` (raw: https://raw.githubusercontent.com/thearpankumar/Talentra-Hackathon/master/infra/certs/main-ca.crt)
- backup cluster: `infra/certs/backup-ca.crt`

If the CA is ever regenerated, fetch the fresh copy from the host: `scp ubuntu@34.235.96.187:/opt/talentra/repo/infra/main-db/certs-public/ca.crt main-ca.crt` (and `backup-db/certs-public/ca.crt`).

## 2. Connections
| Name | Host | Port | Database | User | Notes |
|---|---|---|---|---|---|
| Talentra main WRITE (admin) | `db.talentra.thearpanhq.in` | 26257 | `talentra` | `talentra_admin` | schema work, DDL; always lands on the preferred leaseholder node |
| Talentra main WRITE (app) | `db.talentra.thearpanhq.in` | 26257 | `talentra` | `talentra_app_rw` | application read/write |
| Talentra main READ (read-only) | `db.talentra.thearpanhq.in` | 26259 | `talentra` | `talentra_app_ro` (or `talentra_analyst`) | forced read-only and follower reads (data is about 5 s stale) |
| Talentra BACKUP copy | `backup.talentra.thearpanhq.in` | 26258 | `talentra` | `backup_admin` | restored copy of main; browse only, do not write |

Use `talentra_admin` only when you need admin rights. For looking at data, use the read-only user on port 26259. Passwords are in the credentials file under the matching `TALENTRA_*_PASSWORD` / `BACKUP_ADMIN_PASSWORD` keys.

## 3. DBeaver settings (each connection)
1. New Database Connection -> **PostgreSQL**.
2. **Main** tab: Connect by *Host*; Host, Port, Database as in the table; Authentication *Database Native*; Username and Password from the table / credentials file.
3. **SSL** tab: tick **Use SSL**; **SSL mode = `verify-full`**; **Root certificate** = the downloaded `main-ca.crt` (use `backup-ca.crt` for the backup connection). Leave client certificate, client key and CRL empty. SSL factory: default.
4. **PostgreSQL** tab: untick **Show all databases** (hides `system`/`postgres` noise); optionally set the bootstrap query (Connection settings -> Initialization) `SET allow_unsafe_internals = true;` only if you need `crdb_internal` browsing.
5. Test Connection. The driver is downloaded on first use.

Troubleshooting: *PKIX path building failed* or *hostname does not match* means the wrong root certificate file or an IP address instead of the domain; always use the host names above (the server certificate lists them). *password authentication failed* means the wrong user/password for that cluster.

## 4. psql examples
```
psql "host=db.talentra.thearpanhq.in port=26257 dbname=talentra user=talentra_admin sslmode=verify-full sslrootcert=infra/certs/main-ca.crt"
psql "host=db.talentra.thearpanhq.in port=26259 dbname=talentra user=talentra_app_ro sslmode=verify-full sslrootcert=infra/certs/main-ca.crt"
psql "host=backup.talentra.thearpanhq.in port=26258 dbname=talentra user=backup_admin sslmode=verify-full sslrootcert=infra/certs/backup-ca.crt"
```
Set `PGPASSWORD` from the credentials file rather than typing it on the command line.
