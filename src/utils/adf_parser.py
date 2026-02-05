"""Atlassian Document Format (ADF) parser for Jira descriptions."""

from typing import Any, Dict, List


def parse_adf_to_text(adf_content: Any) -> str:
    """Parse Atlassian Document Format to plain text.

    Args:
        adf_content: ADF content (dict or string)

    Returns:
        Plain text representation
    """
    if isinstance(adf_content, str):
        return adf_content

    if not isinstance(adf_content, dict):
        return str(adf_content)

    # Handle ADF document structure
    if adf_content.get("type") == "doc":
        content_nodes = adf_content.get("content", [])
        return parse_nodes(content_nodes)

    return str(adf_content)


def parse_nodes(nodes: List[Dict[str, Any]], indent: int = 0) -> str:
    """Parse ADF content nodes recursively.

    Args:
        nodes: List of ADF nodes
        indent: Current indentation level

    Returns:
        Plain text representation
    """
    result: List[str] = []
    indent_str = "  " * indent
    prev_type: str | None = None

    for node in nodes:
        node_type = node.get("type", "")

        # Add spacing between different block types
        if result and prev_type and should_add_spacing(prev_type, node_type):
            result.append("")

        if node_type == "paragraph":
            text = parse_inline_content(node.get("content", []))
            if text.strip():
                result.append(indent_str + text)

        elif node_type == "heading":
            level = node.get("attrs", {}).get("level", 1)
            text = parse_inline_content(node.get("content", []))
            prefix = "#" * level
            result.append(f"{indent_str}{prefix} {text}")

        elif node_type == "bulletList":
            list_items = parse_list_items(node.get("content", []), indent, bullet="•")
            result.extend(list_items)

        elif node_type == "orderedList":
            list_items = parse_list_items(node.get("content", []), indent, numbered=True)
            result.extend(list_items)

        elif node_type == "codeBlock":
            language = node.get("attrs", {}).get("language", "")
            code = parse_inline_content(node.get("content", []))
            result.append(f"{indent_str}```{language}")
            result.append(code)
            result.append(f"{indent_str}```")

        elif node_type == "blockquote":
            quoted = parse_nodes(node.get("content", []), indent + 1)
            result.append(f"{indent_str}> {quoted}")

        elif node_type == "rule":
            result.append(indent_str + "---")

        prev_type = node_type

    return "\n".join(result)


def should_add_spacing(prev_type: str, curr_type: str) -> bool:
    """Determine if spacing should be added between node types.

    Args:
        prev_type: Previous node type
        curr_type: Current node type

    Returns:
        True if spacing should be added
    """
    # Add spacing after headings
    if prev_type == "heading":
        return True

    # Add spacing before headings (except after another heading)
    if curr_type == "heading" and prev_type != "heading":
        return True

    # Add spacing after lists
    if prev_type in ("bulletList", "orderedList") and curr_type not in ("bulletList", "orderedList"):
        return True

    # Add spacing before lists
    if curr_type in ("bulletList", "orderedList") and prev_type not in ("bulletList", "orderedList"):
        return True

    # Add spacing around code blocks
    if prev_type == "codeBlock" or curr_type == "codeBlock":
        return True

    # Add spacing around blockquotes
    if prev_type == "blockquote" or curr_type == "blockquote":
        return True

    # Add spacing around rules
    if prev_type == "rule" or curr_type == "rule":
        return True

    return False


def parse_list_items(
    items: List[Dict[str, Any]], indent: int = 0, bullet: str = "•", numbered: bool = False
) -> List[str]:
    """Parse list items.

    Args:
        items: List item nodes
        indent: Current indentation level
        bullet: Bullet character for unordered lists
        numbered: Whether to use numbers for ordered lists

    Returns:
        List of formatted list items
    """
    result = []
    counter = 1
    indent_str = "  " * indent

    for item in items:
        if item.get("type") == "listItem":
            content = item.get("content", [])

            if numbered:
                prefix = f"{counter}."
                counter += 1
            else:
                prefix = bullet

            # Parse the list item content
            item_text = parse_nodes(content, indent + 1)

            # Handle nested lists by preserving indentation
            if item_text.strip():
                lines = item_text.split("\n")
                first_line = lines[0].lstrip()
                result.append(f"{indent_str}{prefix} {first_line}")

                # Add remaining lines with proper indentation
                for line in lines[1:]:
                    if line.strip():
                        result.append(line)

    return result


def parse_inline_content(content: List[Dict[str, Any]]) -> str:
    """Parse inline content (text with marks).

    Args:
        content: List of inline content nodes

    Returns:
        Plain text with markdown-style formatting
    """
    result = []

    for item in content:
        item_type = item.get("type", "")

        if item_type == "text":
            text = item.get("text", "")
            marks = item.get("marks", [])

            # Apply markdown-style formatting based on marks
            for mark in marks:
                mark_type = mark.get("type", "")
                if mark_type == "strong":
                    text = f"**{text}**"
                elif mark_type == "em":
                    text = f"*{text}*"
                elif mark_type == "code":
                    text = f"`{text}`"
                elif mark_type == "strike":
                    text = f"~~{text}~~"
                elif mark_type == "link":
                    href = mark.get("attrs", {}).get("href", "")
                    text = f"[{text}]({href})"

            result.append(text)

        elif item_type == "hardBreak":
            result.append("\n")

        elif item_type == "mention":
            text = item.get("attrs", {}).get("text", "@unknown")
            result.append(text)

    return "".join(result)
