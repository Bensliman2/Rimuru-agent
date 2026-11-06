"""Remote hands (article s5.2): Rimuru thinks here; a session can work on another machine.

On the other machine you run a small program - `python run.py hands --connect
http://<this PC>:8810 --code <code> --root <folder>` - which connects OUT to
Rimuru (so nothing opens on that machine), pairs once with a one-time code, and
then does exactly two things, inside the folders it shares and nowhere else:

  - files: list, read, write - with the same rules as on this computer
  - commands: in its own Docker sandbox; without Docker it runs none, unless
    you started it with --allow-host-commands

Rimuru listens for these machines on a port of its own (8810), which answers
nothing but this one connection; the app itself still answers this computer
only. Pairing codes work once and expire after 10 minutes; each machine then
holds a secret Rimuru keeps only as a hash; a machine can be removed any time.
"""
