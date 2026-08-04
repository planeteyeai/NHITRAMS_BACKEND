"""Username → project details lookup for DPR and related workflows.

Views must call get_project_details(username) only. Swap the active
provider below when the data source changes (MySQL, Postgres, Excel, API).
"""

from __future__ import annotations

from typing import Protocol


class ProjectDetailsNotFound(Exception):
    """Raised when no project assignment exists for the given username."""


class ProjectDetailsProvider(Protocol):
    def get(self, username: str) -> dict:
        """Return project details for username or raise ProjectDetailsNotFound."""


class DatabaseProjectProvider:
    """Reads assignments from the UserProjectAssignment table."""

    def get(self, username: str) -> dict:
        from inventory.models import UserProjectAssignment

        try:
            assignment = UserProjectAssignment.objects.get(username=username)
        except UserProjectAssignment.DoesNotExist as exc:
            raise ProjectDetailsNotFound(
                f'No project assignment found for user "{username}".'
            ) from exc

        return {
            'project_name': assignment.project_name,
            'start_chainage': str(assignment.start_chainage),
            'end_chainage': str(assignment.end_chainage),
            'total_project_length': str(assignment.total_project_length),
        }


def _active_provider() -> ProjectDetailsProvider:
    """Return the configured project-details provider.

    Change this single function (or wire settings) when switching backends.
    """
    return DatabaseProjectProvider()


def get_project_details(username: str) -> dict:
    """Resolve project metadata for an authenticated username.

    Returns:
        {
            "project_name": "...",
            "start_chainage": "...",
            "end_chainage": "...",
            "total_project_length": "...",
        }

    Raises:
        ProjectDetailsNotFound: when the user has no assigned project.
        ValueError: when username is empty.
    """
    if not username or not str(username).strip():
        raise ValueError('username is required to resolve project details.')

    return _active_provider().get(str(username).strip())
