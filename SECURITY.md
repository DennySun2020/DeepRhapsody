# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly:

1. **Do NOT open a public issue**
2. Email the maintainers at sunkuijie@gmail.com
3. Include steps to reproduce the vulnerability
4. Allow reasonable time for a fix before public disclosure

## Security Considerations

### Remote MCP Server

The MCP server's SSE transport mode can expose debug capabilities over HTTP.
If you use remote debugging:

- **Always run behind a reverse proxy with TLS (HTTPS)**
- **Add authentication** (the MCP server does not include auth by default)
- **Restrict network access** to trusted clients only
- **Never expose on public internet** without proper security controls

### Debug Server Ports

Debug session scripts listen on localhost TCP ports. These are bound to
`127.0.0.1` by default and are not accessible from other machines.

### Process Execution

NeuralDebug launches debuggers and compilers as subprocesses. The target
program runs with the same permissions as the user running NeuralDebug.
Do not debug untrusted code without sandboxing.
