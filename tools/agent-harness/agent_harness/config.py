"""Load strict JSON-subset YAML policies using only the standard library."""

from dataclasses import dataclass
import json
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Mapping, Optional, Tuple


class ConfigError(ValueError):
    """Configuration cannot be trusted or interpreted safely."""


@dataclass(frozen=True)
class GateDefinition:
    id: str
    command: Tuple[str, ...]
    cwd: str
    timeout_seconds: int
    execution_tier: str = "local_integration"
    reuse_policy: str = "head"
    input_patterns: Tuple[str, ...] = (".",)
    summary_format: str = "generic"


def validate_gate_id(value):
    if (not isinstance(value, str) or len(value) > 64
            or re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", value) is None
            or value.startswith("sk-")):
        raise ConfigError("Unsafe Gate identifier")
    return value


@dataclass(frozen=True)
class GateMatrix:
    version: int
    gates: Tuple[GateDefinition, ...]
    profiles: Mapping[str, Tuple[str, ...]]
    paths: Mapping[str, Tuple[str, ...]]


@dataclass(frozen=True)
class PathRule:
    id: str
    patterns: Tuple[str, ...]
    operations: Tuple[str, ...]
    action: str
    generator: Optional[Tuple[str, ...]] = None


@dataclass(frozen=True)
class PolicyConfig:
    version: int
    rules: Tuple[PathRule, ...]


@dataclass(frozen=True)
class HistoryBoundaryPolicy:
    allowed_shallow_commits: Tuple[str, ...]
    trusted_head_refs: Tuple[str, ...] = ()


@dataclass(frozen=True)
class GovernanceTransition:
    version: int
    id: str
    source_plan: str
    target_plan: str


def load_governance_transition(path: Path) -> GovernanceTransition:
    document = _read(path)
    _object(document, ("version", "governanceTransition"), (
        "activePlan", "deliveryManifest", "deliverySyncPaths", "documentation",
        "expectedRepository", "fixture", "ledger", "repositoryAttestation",
        "secretPolicy", "historyBoundary",
    ))
    _version(document)
    transition = _object(document["governanceTransition"],
                         ("version", "id", "sourcePlan", "targetPlan"))
    if (type(transition["version"]) is not int or transition["version"] != 1
            or re.fullmatch(r"[a-z][a-z0-9-]{2,63}", transition["id"]) is None):
        raise ConfigError("Unsupported governance transition")
    source = _relative(transition["sourcePlan"])
    target = _relative(transition["targetPlan"])
    if (not PurePosixPath(source).is_relative_to("docs/superpowers/plans")
            or not PurePosixPath(target).is_relative_to("docs/superpowers/plans")
            or source == target):
        raise ConfigError("Unsafe governance transition plans")
    return GovernanceTransition(1, transition["id"], source, target)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError("Duplicate object key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ConfigError("Non-finite JSON number")


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"),
                          object_pairs_hook=_unique_object,
                          parse_constant=_reject_constant)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ConfigError("Cannot read JSON-subset policy") from error


def _object(value, required, optional=()):
    if not isinstance(value, dict):
        raise ConfigError("Expected an object")
    keys = set(value)
    if not set(required) <= keys or keys - set(required) - set(optional):
        raise ConfigError("Missing or unknown configuration keys")
    return value


def _text(value):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ConfigError("Expected a non-empty string without NUL")
    return value


def _array(value):
    if not isinstance(value, list) or not value:
        raise ConfigError("Expected a non-empty array")
    return value


def _strings(value):
    return tuple(_text(item) for item in _array(value))


def _relative(value):
    value = _text(value)
    path = PurePosixPath(value)
    if (path.is_absolute() or PureWindowsPath(value).drive or "\\" in value
            or ".." in path.parts):
        raise ConfigError("Expected a repository-relative path without traversal")
    return value


def _paths(value):
    return tuple(_relative(item) for item in _array(value))


def _version(document):
    if type(document["version"]) is not int or document["version"] != 1:
        raise ConfigError("Unsupported configuration version")


def _unique_identifier(value, seen):
    identifier = _text(value)
    if identifier in seen:
        raise ConfigError("Duplicate identifier")
    seen.add(identifier)
    return identifier


def load_gate_matrix(path: Path) -> GateMatrix:
    document = _object(_read(path), ("version", "gates", "profiles"))
    version = document["version"]
    if type(version) is not int or version not in {1, 2}:
        raise ConfigError("Unsupported Gate Matrix version")
    gates, identifiers = [], set()
    for item in _array(document["gates"]):
        metadata = ("execution_tier", "reuse_policy", "input_patterns", "summary_format")
        required = ("id", "command", "cwd", "timeout_seconds")
        item = _object(item, required + metadata if version == 2 else required)
        timeout = item["timeout_seconds"]
        if type(timeout) is not int or timeout <= 0:
            raise ConfigError("Gate timeout must be a positive integer")
        execution_tier = item.get("execution_tier", "local_integration")
        reuse_policy = item.get("reuse_policy", "head")
        summary_format = item.get("summary_format", "generic")
        if execution_tier not in {"local_fast", "local_integration", "github_full"}:
            raise ConfigError("Unsupported Gate execution tier")
        if reuse_policy not in {"head", "content"}:
            raise ConfigError("Unsupported Gate reuse policy")
        if summary_format not in {"generic", "unittest", "surefire", "vitest", "playwright"}:
            raise ConfigError("Unsupported Gate summary format")
        input_patterns = _paths(item["input_patterns"]) if version == 2 else (".",)
        gates.append(GateDefinition(
            _unique_identifier(validate_gate_id(item["id"]), identifiers), _strings(item["command"]),
            _relative(item["cwd"]), timeout, execution_tier, reuse_policy,
            input_patterns, summary_format,
        ))
    raw_profiles = document["profiles"]
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        raise ConfigError("Expected non-empty profiles object")
    profiles, paths = {}, {}
    for name, profile in raw_profiles.items():
        name = _text(name)
        profile = _object(profile, ("paths", "gates"))
        references = _strings(profile["gates"])
        if len(set(references)) != len(references) or set(references) - identifiers:
            raise ConfigError("Duplicate or unknown gate reference")
        profiles[name], paths[name] = references, _paths(profile["paths"])
    return GateMatrix(version, tuple(gates), MappingProxyType(profiles),
                      MappingProxyType(paths))


def load_protected_paths(path: Path) -> PolicyConfig:
    document = _object(_read(path), ("version", "rules"))
    _version(document)
    rules, identifiers = [], set()
    actions = {"deny", "approval_required", "review_required", "generated"}
    for item in _array(document["rules"]):
        item = _object(item, ("id", "patterns", "operations", "action"), ("generator",))
        action = _text(item["action"])
        if action not in actions:
            raise ConfigError("Unsupported path rule action")
        operations = _strings(item["operations"])
        if (set(operations) - {"add", "modify", "delete"}
                or len(set(operations)) != len(operations)):
            raise ConfigError("Unsupported or duplicate path operation")
        generator = _strings(item["generator"]) if "generator" in item else None
        if (action == "generated") != (generator is not None):
            raise ConfigError("Only generated rules require a generator command")
        rules.append(PathRule(
            _unique_identifier(item["id"], identifiers), _paths(item["patterns"]),
            operations, action, generator,
        ))
    return PolicyConfig(1, tuple(rules))


def load_history_boundary_policy(path: Path) -> HistoryBoundaryPolicy:
    optional = (
        "activePlan", "deliveryManifest", "deliverySyncPaths", "documentation",
        "governanceTransition",
        "expectedRepository", "fixture", "ledger", "repositoryAttestation",
        "secretPolicy",
    )
    document = _object(_read(path), ("version", "historyBoundary"), optional)
    _version(document)
    if "governanceTransition" in document:
        load_governance_transition(path)
    boundary = _object(document["historyBoundary"],
                       ("allowedShallowCommits", "trustedHeadRefs"))
    commits = _array(boundary["allowedShallowCommits"])
    if (any(not isinstance(commit, str)
            or re.fullmatch(r"[0-9a-f]{40}", commit) is None for commit in commits)
            or len(set(commits)) != len(commits)):
        raise ConfigError("History boundary commits must be unique full SHA-1 identifiers")
    refs = _array(boundary["trustedHeadRefs"])
    if (any(not isinstance(ref, str)
            or re.fullmatch(r"refs/(?:heads|remotes)/[A-Za-z0-9][A-Za-z0-9._/-]*", ref) is None
            or ".." in ref or "//" in ref or "@{" in ref
            or ref.endswith(("/", ".", ".lock")) for ref in refs)
            or len(set(refs)) != len(refs)):
        raise ConfigError("History boundary refs must be unique canonical head refs")
    return HistoryBoundaryPolicy(tuple(commits), tuple(refs))
