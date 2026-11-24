# Referência para produção (PostgreSQL + PostGIS)
Estes arquivos (alembic.ini e migrations/) são o schema da Fase 1 **em PostgreSQL + PostGIS**.
A versão local usa SQLite (`backend/app/schema.py`) e **não usa estes arquivos**.
Quando formos para produção com mapa em escala, o schema e as consultas serão portados de volta para PostgreSQL
(a parte espacial, `location geography(Point,4326)` com índice GiST, não existe no SQLite).
