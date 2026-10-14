# payload-cache-service

A FastAPI microservice that builds payloads by interleaving two lists of
transformed strings. Results of the (slow, external) transformer are cached
in a database, so each distinct string is transformed only once.
