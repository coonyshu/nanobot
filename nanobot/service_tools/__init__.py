"""
nanobot Custom Tools.

- ActionManager: 前端动作动态注册与派发
"""

from .action_manager import ActionManager, action_manager

__all__ = [
    "ActionManager",
    "action_manager",
]
