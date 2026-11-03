# Usa uma imagem oficial do Python
FROM python:3.11

# Instala o Playwright como administrador para baixar as dependências do sistema
RUN pip install --no-cache-dir playwright
RUN playwright install-deps chromium

# Configuração obrigatória de segurança do Hugging Face (cria o utilizador 1000)
RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:$PATH"

WORKDIR /home/user/app

# Copia a lista de bibliotecas e instala
COPY --chown=user:user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Baixa apenas o Chromium invisível para o utilizador
RUN playwright install chromium

# Copia o resto do seu código (incluindo a pasta Itaparica)
COPY --chown=user:user . .

# O Hugging Face usa obrigatoriamente a porta 7860
ENV PORT=7860
EXPOSE 7860

# Inicia o seu bot (Ajustado para encontrar o ficheiro dentro da pasta)
CMD ["python", "Itaparica/bot_travessia.py"]
