# MSK Security Model

## 1. Principles

1. **Zero Secret Persistence**: Under no circumstance are secret keys, passwords, bearer tokens, private keys, or credentials stored inside `.msk/msk.db` or `.msk/*.json`.
2. **Local Analysis Only**: Core analysis operates offline without external network communication.
3. **Boundary Integrity**: In future AI phases, context passed to LLMs must pass through strict policy filtering and redaction.
4. **Symlink Traversal Protection**: The file scanner rejects and skips symlinks that point outside the project boundary or create directory recursion loops.
5. **Safe Subprocess Execution**: Any interaction with native Git commands uses parameterized lists rather than raw shell strings to prevent injection.
