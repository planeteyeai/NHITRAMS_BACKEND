"""User management domain services."""

from __future__ import annotations

import logging
import math
from typing import Any

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from .constants import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    SORTABLE_FIELDS,
    USER_ROLE_SUPER_ADMIN,
    allowed_access_types_for_role,
)
from .models import UserProfile

logger = logging.getLogger(__name__)

User = get_user_model()


def _duplicate_username_error(username: str | None = None) -> ValidationError:
    return ValidationError(
        {
            'username': [
                f'Username already exists: {username}'.strip(': ')
                if username
                else 'Username already exists.'
            ]
        },
        code='duplicate_username',
    )


def _duplicate_email_error(email: str | None = None) -> ValidationError:
    return ValidationError(
        {
            'email': [
                f'Email already exists: {email}'.strip(': ')
                if email
                else 'Email already exists.'
            ]
        },
        code='duplicate_email',
    )


def _invalid_role_access_error() -> ValidationError:
    return ValidationError(
        {
            'access_type': [
                'Access type is not valid for the selected user role.'
            ]
        },
        code='invalid_role_access',
    )


def _invalid_role_error() -> ValidationError:
    return ValidationError({'role': ['Invalid user role.']}, code='invalid_role')


def get_actor_profile(username: str) -> UserProfile | None:
    try:
        return UserProfile.objects.select_related('user').get(user__username=username)
    except UserProfile.DoesNotExist:
        return None


def _manager_profile(
    *,
    actor_username: str | None = None,
    actor_profile: UserProfile | None = None,
) -> UserProfile:
    if actor_profile is not None:
        if not actor_profile.user.is_active:
            raise PermissionDenied('You do not have permission to manage users.')
        from .constants import USER_MANAGER_ROLES

        if actor_profile.role not in USER_MANAGER_ROLES:
            raise PermissionDenied('You do not have permission to manage users.')
        return actor_profile
    return _assert_can_manage_users(actor_username=actor_username or '')


def _assert_can_manage_users(*, actor_username: str) -> UserProfile:
    profile = get_actor_profile(actor_username)
    if profile is None or not profile.user.is_active:
        raise PermissionDenied('You do not have permission to manage users.')
    from .constants import USER_MANAGER_ROLES

    if profile.role not in USER_MANAGER_ROLES:
        raise PermissionDenied('You do not have permission to manage users.')
    return profile


def _assert_can_assign_role(*, actor_profile: UserProfile, target_role: str) -> None:
    if target_role == USER_ROLE_SUPER_ADMIN and actor_profile.role != USER_ROLE_SUPER_ADMIN:
        raise PermissionDenied('Only SuperAdmin can assign the SuperAdmin role.')


def _email_exists(email: str, *, exclude_user_id: int | None = None) -> bool:
    queryset = User.objects.filter(email__iexact=email)
    if exclude_user_id is not None:
        queryset = queryset.exclude(pk=exclude_user_id)
    return queryset.exists()


def _resolve_effective_fields(
    *,
    user: User | None,
    profile: UserProfile | None,
    validated: dict,
    partial: bool,
) -> dict:
    access_type_provided = 'access_type' in validated

    if user is None:
        effective = dict(validated)
    elif partial:
        effective = {
            'username': validated.get('username', user.username),
            'first_name': validated.get('first_name', user.first_name),
            'last_name': validated.get('last_name', user.last_name),
            'email': validated.get('email', user.email),
            'phone_number': (
                validated['phone_number']
                if 'phone_number' in validated
                else profile.phone_number
            ),
            'role': validated.get('role', profile.role),
            'access_type': (
                validated['access_type']
                if 'access_type' in validated
                else profile.access_type
            ),
        }
        if 'password' in validated:
            effective['password'] = validated['password']
    else:
        effective = dict(validated)

    role = effective['role']
    allowed = allowed_access_types_for_role(role)
    if not allowed:
        raise _invalid_role_error()

    access_type = effective['access_type']
    if len(allowed) == 1:
        required_access = next(iter(allowed))
        if access_type_provided and access_type != required_access:
            raise _invalid_role_access_error()
        effective['access_type'] = required_access
    elif access_type not in allowed:
        if partial and not access_type_provided and len(allowed) == 1:
            effective['access_type'] = next(iter(allowed))
        else:
            raise _invalid_role_access_error()

    return effective


def _base_queryset(*, include_inactive: bool = False) -> QuerySet:
    queryset = User.objects.select_related('profile').filter(profile__isnull=False)
    if not include_inactive:
        queryset = queryset.filter(is_active=True)
    return queryset


def get_user_or_404(user_id: int, *, include_inactive: bool = False) -> User:
    queryset = _base_queryset(include_inactive=include_inactive)
    try:
        return queryset.get(pk=user_id)
    except User.DoesNotExist as exc:
        raise NotFound('User not found.') from exc


def serialize_user(user: User) -> dict[str, Any]:
    profile = user.profile
    return {
        'id': user.id,
        'username': user.username,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'email': user.email,
        'phone_number': profile.phone_number,
        'role': profile.role,
        'access_type': profile.access_type,
        'is_active': user.is_active,
        'created_by': profile.created_by,
        'updated_by': profile.updated_by,
        'created_at': profile.created_at,
        'updated_at': profile.updated_at,
    }


def _apply_filters(queryset: QuerySet, filters: dict) -> QuerySet:
    role = filters.get('role')
    if role is not None and str(role).strip() != '':
        queryset = queryset.filter(profile__role__iexact=str(role).strip())

    access_type = filters.get('access_type')
    if access_type is not None and str(access_type).strip() != '':
        queryset = queryset.filter(profile__access_type__iexact=str(access_type).strip())

    return queryset


def _apply_search(queryset: QuerySet, search: str) -> QuerySet:
    text = (search or '').strip()
    if not text:
        return queryset
    return queryset.filter(
        Q(username__icontains=text)
        | Q(first_name__icontains=text)
        | Q(last_name__icontains=text)
        | Q(email__icontains=text)
        | Q(profile__phone_number__icontains=text)
        | Q(profile__role__icontains=text)
        | Q(profile__access_type__icontains=text)
    )


def _apply_sort(queryset: QuerySet, sort_by: str, sort_order: str) -> QuerySet:
    field = (sort_by or 'created_at').strip()
    if field not in SORTABLE_FIELDS:
        field = 'created_at'

    prefix = '' if (sort_order or 'desc').lower() == 'asc' else '-'
    if field in {'role', 'access_type', 'created_at', 'updated_at'}:
        order_field = f'{prefix}profile__{field}'
    else:
        order_field = f'{prefix}{field}'
    return queryset.order_by(order_field, '-id')


def list_users(*, query: dict, actor_username: str, actor_profile: UserProfile | None = None) -> dict[str, Any]:
    _manager_profile(actor_username=actor_username, actor_profile=actor_profile)

    page = int(query.get('page') or 1)
    page_size = int(query.get('page_size') or DEFAULT_PAGE_SIZE)
    page_size = min(max(page_size, 1), MAX_PAGE_SIZE)
    page = max(page, 1)

    is_active_filter = query.get('is_active')
    if is_active_filter is None:
        queryset = User.objects.select_related('profile').filter(
            profile__isnull=False,
            is_active=True,
        )
    else:
        queryset = User.objects.select_related('profile').filter(
            profile__isnull=False,
            is_active=is_active_filter,
        )

    filters = dict(query)
    filters.pop('is_active', None)
    queryset = _apply_filters(queryset, filters)
    queryset = _apply_search(queryset, query.get('search') or '')
    queryset = _apply_sort(
        queryset,
        query.get('sort_by') or 'created_at',
        query.get('sort_order') or 'desc',
    )

    total = queryset.count()
    total_pages = math.ceil(total / page_size) if total else 0
    if total_pages and page > total_pages:
        page = total_pages

    offset = (page - 1) * page_size
    rows = list(queryset[offset : offset + page_size])

    return {
        'rows': rows,
        'pagination': {
            'page': page,
            'page_size': page_size,
            'total': total,
            'total_pages': total_pages,
        },
    }


@transaction.atomic
def create_user(
    *,
    validated: dict,
    actor_username: str,
    actor_profile: UserProfile | None = None,
) -> User:
    actor_profile = _manager_profile(
        actor_username=actor_username,
        actor_profile=actor_profile,
    )
    payload = _resolve_effective_fields(
        user=None,
        profile=None,
        validated=validated,
        partial=False,
    )
    _assert_can_assign_role(actor_profile=actor_profile, target_role=payload['role'])

    if _email_exists(payload['email']):
        raise _duplicate_email_error(payload['email'])

    try:
        user = User.objects.create_user(
            username=payload['username'],
            email=payload['email'],
            password=payload['password'],
            first_name=payload['first_name'],
            last_name=payload['last_name'],
        )
    except IntegrityError as exc:
        if 'email' in str(exc).lower():
            logger.info(
                'Duplicate email blocked on create email=%s by actor=%s',
                payload.get('email'),
                actor_username,
            )
            raise _duplicate_email_error(payload.get('email')) from None
        logger.info(
            'Duplicate username blocked on create username=%s by actor=%s',
            payload.get('username'),
            actor_username,
        )
        raise _duplicate_username_error(payload.get('username')) from None

    profile = UserProfile.objects.create(
        user=user,
        phone_number=payload['phone_number'],
        role=payload['role'],
        access_type=payload['access_type'],
        created_by=actor_username,
        updated_by=actor_username,
    )
    user.profile = profile
    return user


@transaction.atomic
def update_user(
    *,
    user: User,
    validated: dict,
    actor_username: str,
    partial: bool,
    actor_profile: UserProfile | None = None,
) -> User:
    actor_profile = _manager_profile(
        actor_username=actor_username,
        actor_profile=actor_profile,
    )
    profile = user.profile
    payload = _resolve_effective_fields(
        user=user,
        profile=profile,
        validated=validated,
        partial=partial,
    )
    _assert_can_assign_role(actor_profile=actor_profile, target_role=payload['role'])

    if payload['email'] != user.email and _email_exists(payload['email'], exclude_user_id=user.id):
        raise _duplicate_email_error(payload['email'])

    user.username = payload['username']
    user.first_name = payload['first_name']
    user.last_name = payload['last_name']
    user.email = payload['email']

    if 'password' in payload and payload['password']:
        user.set_password(payload['password'])

    profile.phone_number = payload['phone_number']
    profile.role = payload['role']
    profile.access_type = payload['access_type']
    profile.updated_by = actor_username

    try:
        user.save()
        profile.save()
    except IntegrityError as exc:
        if 'email' in str(exc).lower():
            logger.info(
                'Duplicate email blocked on update id=%s email=%s by actor=%s',
                user.id,
                payload.get('email'),
                actor_username,
            )
            raise _duplicate_email_error(payload.get('email')) from None
        logger.info(
            'Duplicate username blocked on update id=%s username=%s by actor=%s',
            user.id,
            payload.get('username'),
            actor_username,
        )
        raise _duplicate_username_error(payload.get('username')) from None

    return user


@transaction.atomic
def deactivate_user(
    *,
    user: User,
    actor_username: str,
    actor_profile: UserProfile | None = None,
) -> None:
    actor_profile = _manager_profile(
        actor_username=actor_username,
        actor_profile=actor_profile,
    )

    if user.username == actor_username:
        raise PermissionDenied('You cannot deactivate your own account.')

    if (
        user.profile.role == USER_ROLE_SUPER_ADMIN
        and actor_profile.role != USER_ROLE_SUPER_ADMIN
    ):
        raise PermissionDenied('Only SuperAdmin can deactivate a SuperAdmin account.')

    active_superadmins = User.objects.filter(
        is_active=True,
        profile__role=USER_ROLE_SUPER_ADMIN,
    ).exclude(pk=user.pk).count()
    if user.profile.role == USER_ROLE_SUPER_ADMIN and active_superadmins == 0:
        raise PermissionDenied('Cannot deactivate the last active SuperAdmin account.')

    user.is_active = False
    user.save(update_fields=['is_active'])
    user.profile.updated_by = actor_username
    user.profile.save(update_fields=['updated_by', 'updated_at'])
