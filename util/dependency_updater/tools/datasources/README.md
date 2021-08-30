# Datasources

Credentials can be pre-configured via the root level
`config.yaml` or via environment variables of the same name. If credentials are not supplied or are
incorrect, the user will be prompted at runtime to re-enter valid credentials the datasource.

**Datasources are available on the Config object as `config.datasources`.**

### Gerrit Code Review Client

Accessible via the config object @ `config.datasources.gerrit_client`

**Authentication is mandatory to create changes, auto-approve, or stage changes.** The tool defaults
to simulating updates if credentials are not supplied.

Provides a sanitized JSON response from gerrit queries, based on
the [Gerrit API reference](https://gerrit-review.googlesource.com/Documentation/rest-api.html)
