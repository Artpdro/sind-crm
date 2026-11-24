from typing import Any, Literal

from pydantic import EmailStr, Field, field_validator

from app.modules.auth.schemas import Strict
from app.modules.companies import normalizers as nz


class EmpresaFields(Strict):
    """Campos editáveis. Campos de sistema (id, coordenadas, status de geocodificação, autor, versão...)
    NÃO existem aqui: qualquer um deles no corpo da requisição é rejeitado (extra=forbid)."""
    nome: str | None = Field(default=None, max_length=300)
    nome_fantasia: str | None = Field(default=None, max_length=300)
    cnpj: str | None = Field(default=None, max_length=30)
    email: EmailStr | None = None
    emails_adicionais: list[EmailStr] | None = Field(default=None, max_length=20)
    celular: str | None = Field(default=None, max_length=30)
    fone: str | None = Field(default=None, max_length=30)
    cep: str | None = Field(default=None, max_length=15)
    rua: str | None = Field(default=None, max_length=300)
    numero: str | None = Field(default=None, max_length=60)
    complemento: str | None = Field(default=None, max_length=300)
    bairro: str | None = Field(default=None, max_length=150)
    cidade: str | None = Field(default=None, max_length=150)
    estado: str | None = Field(default=None, max_length=2)
    porte_id: int | None = Field(default=None, ge=1)
    tipo_empresa_id: int | None = Field(default=None, ge=1)
    situacao_id: int | None = Field(default=None, ge=1)
    categoria_ids: list[int] | None = Field(default=None, max_length=50)
    informacoes_adicionais: str | None = Field(default=None, max_length=20000)
    campos: dict[str, Any] | None = None

    @field_validator("*", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("cnpj")
    @classmethod
    def _cnpj(cls, v):
        return nz.normalize_cnpj(v) if v else None

    @field_validator("celular", "fone")
    @classmethod
    def _phone(cls, v):
        return nz.normalize_phone(v) if v else None

    @field_validator("cep")
    @classmethod
    def _cep(cls, v):
        return nz.normalize_cep(v) if v else None

    @field_validator("estado")
    @classmethod
    def _estado(cls, v):
        v = nz.normalize_estado(v)
        if v and (len(v) != 2 or not v.isalpha()):
            raise ValueError("Estado deve ter a sigla de 2 letras.")
        return v

    @field_validator("categoria_ids")
    @classmethod
    def _cats(cls, v):
        if v is not None and (any(i < 1 for i in v) or len(set(v)) != len(v)):
            raise ValueError("Categorias inválidas ou repetidas.")
        return v


class EmpresaCreate(EmpresaFields):
    nome: str = Field(min_length=1, max_length=300)


class EmpresaUpdate(EmpresaFields):
    version: int = Field(ge=1)  # bloqueio otimista: versão que o usuário estava vendo


class InteracaoIn(Strict):
    tipo: Literal["ligacao", "whatsapp", "email", "visita", "reuniao", "atendimento", "outro"]
    descricao: str = Field(min_length=1, max_length=5000)
    situacao_resultante_id: int | None = Field(default=None, ge=1)
    observacoes: str | None = Field(default=None, max_length=5000)
