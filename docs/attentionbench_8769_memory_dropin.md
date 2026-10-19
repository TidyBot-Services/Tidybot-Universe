# 8769 → isolated Memory Service (operator-applied template)

This file is a template only. It does not change the running `attentionbench-8769` user service. Keep the isolated Memory Service on `127.0.0.1:8878` with its empty test database. Do not connect 8769 to the offline importer account.

## Obtain the authority ID

With the **online** key in the operator's shell, query the authenticated identity endpoint of the isolated service:

```sh
curl --fail --silent --show-error -H "X-Memory-Service-Key: $ATTENTION_MEMORY_API_KEY" http://127.0.0.1:8878/store
```

Use the returned `store_id` as `<store_id>` below. The HTTP client in `attention_memory_service/memory_service_client.py` uses `GET /store`; verify the response before editing the drop-in. Choose an isolated, stable namespace `<ns>` and use the same value when creating the test-seeded note.

## Environment file template

Create a private file outside the repository, readable only by the 8769 service user (for example `~/.config/attentionbench-8769/memory-online.env`, mode 0600):

```ini
ATTENTION_MEMORY_API_KEY=<online_service_key>
ATTENTION_MEMORY_OPERATOR_KEY=<distinct_operator_key>
```

The online key enables retrieval and public catalogue reads. The distinct operator key enables disable, enable, expiry and rollback in `ui_server`'s Memory panel. Both are required for lifecycle actions, along with actor and reason. **Never give 8769 `ATTENTION_MEMORY_IMPORTER_KEY`, an importer key file, or any offline catalog credential.** Do not reuse the importer's environment file.

## `~/.config/systemd/user/attentionbench-8769.service.d/override.conf` template

A copyable sample is [attentionbench-8769.override.conf.example](attentionbench-8769.override.conf.example).

Systemd does not append arguments to `ExecStart`; copy the current command and every existing argument into the placeholder below, then append the three Memory arguments. Preserve its existing executable, wrapper, paths and other environment settings. This sample is intentionally not executable until that command is supplied.

```ini
[Service]
EnvironmentFile=%h/.config/attentionbench-8769/memory-online.env
ExecStart=
ExecStart=<existing 8769 command and all existing arguments> --memory-service-url http://127.0.0.1:8878 --dev-v2-memory-store-id <store_id> --dev-v2-memory-namespace <ns>
```

The applicable code is `benchmarks/attention_harness/ui_server.py`: `--memory-service-url` builds `MemoryView.service` with `ATTENTION_MEMORY_API_KEY` and optional `ATTENTION_MEMORY_OPERATOR_KEY`; the Dev v2 daily Memory configuration requires `--dev-v2-root`, URL, store ID, namespace and online key. The operator must retain the current `--dev-v2-root` argument.

To apply later, inspect the existing unit with `systemctl --user cat attentionbench-8769`, fill in the placeholder, install the private environment file and drop-in, then run `systemctl --user daemon-reload` and `systemctl --user restart attentionbench-8769`. Confirm the live argument list and that the Memory panel can read the isolated catalogue. These commands are instructions for the operator, not run by this change.
