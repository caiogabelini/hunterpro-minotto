# --- Etapa 1: build do bundle ---
FROM node:22-slim AS build
WORKDIR /app

COPY package*.json ./
# `npm ci` em vez de `npm install`: instala exatamente o que está no
# package-lock.json e falha se os dois estiverem fora de sincronia --
# build de produção não deve resolver versões novas por conta própria.
RUN npm ci

COPY . .

# VITE_API_URL fica DELIBERADAMENTE indefinido aqui.
#
# Variável do Vite é assada no bundle em tempo de build, não lida em
# runtime -- então não adianta configurá-la no painel do EasyPanel depois.
# Com ela indefinida, o api.ts usa "" em build de produção, o que gera
# URLs relativas (/api/...) que o proxy do nginx desta mesma imagem
# resolve. Ver o comentário no topo de src/api.ts.
#
# Só defina VITE_API_URL (ex.: --build-arg) se o backend for servido em
# outro domínio -- aí volta a existir CORS e o FRONTEND_ORIGIN do backend
# passa a ser o que faz a app funcionar, não só defesa em profundidade.
RUN npm run build

# --- Etapa 2: nginx servindo o build + proxy pra API ---
FROM nginx:alpine

# Nome/porta do App Service do backend dentro da rede interna do
# EasyPanel. São ENV (não valores fixos no .conf) pra poderem ser
# sobrescritos pelo painel sem rebuild da imagem -- se o serviço tiver
# outro nome, basta ajustar BACKEND_HOST no EasyPanel.
ENV BACKEND_HOST=hunterpro-minotto-backend
ENV BACKEND_PORT=8000

# A imagem oficial do nginx roda envsubst nos arquivos desta pasta no
# boot e escreve o resultado em /etc/nginx/conf.d/default.conf.
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
