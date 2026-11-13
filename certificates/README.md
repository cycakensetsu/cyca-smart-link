# Supabase TLS trust certificate

Public Supabase Root 2021 CA, downloaded from the project Dashboard Database Settings official certificate link:
https://supabase-downloads.s3-ap-southeast-1.amazonaws.com/prod/ssl/prod-ca-2021.crt

SHA-256 fingerprint:
`80:70:25:AD:50:D4:ED:21:9D:2C:9C:7D:29:9C:00:4F:82:4E:B0:0C:F7:F6:5A:FE:F6:07:D0:7B:72:E6:CA:FA`

Valid until 2031-04-26. This is a public certificate, not a private key or account credential. It is used only for the explicitly configured Supabase PostgreSQL connection with `sslmode=verify-full`; it is not installed into the OS/global trust store.

For the existing Streamlit deployment, the private DSN may reference `/mount/src/cyca-smart-link/certificates/supabase-root-2021.crt`. Verify this path in the deployed app before enabling the workflow. Never lower TLS verification to work around connection errors.
