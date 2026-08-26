# ACPs private runtime files

Place the CA-issued files here using these local names:

- `leader-client.pem` and `leader-client-key.pem`: the Leader AIC `clientAuth` identity;
- `partner-client.pem` and `partner-client-key.pem`: the Partner AIC `clientAuth` identity;
- `trust-bundle.pem`: the ACPs CA chain used for peer verification.

Pure Inbox/Group mode does not install a Longyun `serverAuth` certificate and
does not expose a JSON-RPC or 9443 ingress.

All certificate and key files in this directory are ignored by Git. Never
commit EAB credentials, private keys, or issued identity certificates.
