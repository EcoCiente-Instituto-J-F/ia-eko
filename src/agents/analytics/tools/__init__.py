from src.agents.analytics.tools.quizzes import TOOLS as QUIZ_TOOLS
from src.agents.analytics.tools.recycling import TOOLS as RECYCLING_TOOLS
from src.agents.analytics.tools.simulation import TOOLS as SIMULATION_TOOLS
from src.agents.analytics.tools.trust import TOOLS as TRUST_TOOLS
from src.tools.shared.rankings import TOOLS as RANKING_TOOLS

TOOLS = [*RECYCLING_TOOLS, *TRUST_TOOLS, *QUIZ_TOOLS, *SIMULATION_TOOLS, *RANKING_TOOLS]

__all__ = ["TOOLS"]
