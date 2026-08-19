# ACPs private runtime files

Place the CA-issued files here using these local names:

- `client.pem` and `client-key.pem`: the Leader `clientAuth` certificate and key;
- `server.pem` and `server-key.pem`: the Partner `serverAuth` certificate and key;
- `trust-bundle.pem`: the ACPs CA chain used for peer verification.

All certificate and key files in this directory are ignored by Git. Never
commit EAB credentials, private keys, or issued identity certificates.
