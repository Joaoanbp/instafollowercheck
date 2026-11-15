"""Relatórios completos e sugestões para revisão manual."""

from .models import Snapshot, compare

LABELS = {
    "followers": "Quem segue você",
    "following": "Quem você segue",
    "mutual": "Seguidores mútuos",
    "not_following_back": "Você segue, mas não segue você de volta",
    "not_followed_back": "Segue você, mas você não segue de volta",
    "lost_followers": "Seguidores perdidos no período (possíveis unfollows)",
    "stopped_following": "Contas que você deixou de seguir no período",
    "possible_soft_blocks": "Possíveis soft blocks (perdas nas duas direções)",
}


def render_report(current: Snapshot, previous: Snapshot | None) -> str:
    groups = compare(current, previous)
    names = {}
    if previous:
        names.update(previous.following)
        names.update(previous.followers)
    names.update(current.following)
    names.update(current.followers)
    lines = [
        f"=== Monitoramento de @{current.username} ===",
        f"Coleta concluída: {current.captured_at}",
        "Comparação desde: "
        + (previous.captured_at if previous else "primeira coleta (linha de base)"),
        "",
        "Resumo numérico:",
    ]
    lines.extend(f"{label}: {len(groups[key])}" for key, label in LABELS.items())
    lines += [
        "", "Perdas indicam ausência entre snapshots; não confirmam a causa.",
        "Soft block é apenas hipótese: unfollow manual, desativação, exclusão ou bloqueio",
        "também podem produzir perdas. Não é possível determinar intenção ou involuntariedade.",
    ]
    if previous is None:
        lines.append(
            "Histórico indisponível: perdas só serão detectadas na próxima coleta completa."
        )
    for key, label in LABELS.items():
        lines += ["", f"## {label} ({len(groups[key])})"]
        for account_id in sorted(groups[key], key=lambda item: (names[item].lower(), item)):
            lines.append(f"- @{names[account_id]} (ID {account_id})")
        if not groups[key]:
            lines.append("(nenhuma conta)")
    candidates = groups["not_following_back"]
    lines += [
        "", f"## Sugestões para revisar e talvez deixar de seguir ({len(candidates)})",
        "Critério: você segue a conta e ela não segue você de volta.",
        "Decida manualmente: criadores, amigos e marcas podem ser exceções desejadas.",
    ]
    lines.extend(
        f"- @{names[item]} — https://www.instagram.com/{names[item]}/"
        for item in sorted(candidates, key=lambda item: (names[item].lower(), item))
    )
    if not candidates:
        lines.append("(nenhuma sugestão)")
    return "\n".join(lines) + "\n"
