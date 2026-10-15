# Third-party software notices

## PHPBench 0.8.1

Copyright (c) 2005 Frank Denis <j@pureftpd.org>.

The PHPBench source is distributed under the permissive license reproduced in
[`third-party/PHPBENCH-LICENSE.txt`](third-party/PHPBENCH-LICENSE.txt). The
Workshop recipe fetches the 9,482-byte original Pure-FTPd source archive and
verifies MD5 `9ee2e4305e320c7f20a3d81978c8d0dc` and SHA-256
`deff97dd1372405dbad3bf633009ca8446cb9770c5effa40cc45849a461c1b4a` before
applying the local compatibility patch. These upstream archive hashes are
separate from the PTS profile payload hashes.

The patch is derived from the `phpbench-081-patched2.zip` payload used by
Phoronix Test Suite profile `pts/phpbench` 1.1.6. Its `downloads.xml` specifies
MD5 `3e5fa1ad60261ff052bb38f505c8c4e5` and SHA-256
`32503bd4ace0c8429493de864ca48bb16febed867e52b75f4369d7145f797718`. The
patch is retained separately at
[`third-party/phpbench-0.8.1-pts-compat.patch`](third-party/phpbench-0.8.1-pts-compat.patch)
with a pinned SHA-256. The patch changes no license text. It adapts legacy PHP
expressions/functions and removes some regression assertions, as described in
`DESIGN.md`.

The Crucible adapter scripts are licensed under Apache-2.0 in `LICENSE`.
