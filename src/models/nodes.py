from dataclasses import dataclass


@dataclass
class EmpresaNode:
    nome: str
    setor: str


@dataclass
class ResiduoNode:
    nome: str
    categoria: str
    impacto: str


@dataclass
class SolucaoNode:
    nome: str
    descricao: str