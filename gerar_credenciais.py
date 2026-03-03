"""
Gera o arquivo credenciais.xlsx de exemplo.
Execute uma vez para criar o template:
    python gerar_credenciais.py
"""
import pandas as pd

df = pd.DataFrame([
    {"email": "usuario1@email.com", "senha": "senha123"},
    {"email": "usuario2@email.com", "senha": "outrasenha"},
])
df.to_excel("credenciais.xlsx", index=False)
print("credenciais.xlsx criado com sucesso!")
