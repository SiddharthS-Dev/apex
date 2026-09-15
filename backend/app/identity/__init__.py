"""Identity and RBAC bounded context.

Owns users, roles, permissions and their assignments, plus authentication and
the authorisation primitives other contexts depend on. Sits in the foundation
tier of the context map and depends on no other context.
"""
