# Postman export

Postman export is deterministic. It consumes stable plans and test cases; it
does not ask an LLM to write collection JSON or scripts.

The collection contains:

- environment variables such as `baseUrl`;
- dependency setup requests;
- stable happy-path requests;
- deterministic schema cases;
- LLM-proposed executable business cases;
- pre-request generator scripts;
- response extraction scripts;
- status and response assertions.

Python generators and JavaScript generator implementations must share the same
name, parameters, and semantics. Export-time validation checks that every
referenced variable is either initialized, generated, externally provided, or
extracted before use.

Collections are derived artifacts. The source of truth remains the typed data
contracts and stable package.
