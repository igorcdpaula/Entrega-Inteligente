# Roteirizador de Entregas Mobile com correções
import streamlit as st
import pdfplumber
import re
import pandas as pd
import time
import io
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut
from ortools.constraint_solver import pywrapcp, routing_enums_pb2
from haversine import haversine, Unit

# --- Funções auxiliares ---
def normalizar_letras(letras):
    return re.sub(r'\s+', '', letras.strip().upper())

def limpar_endereco(endereco):
    padroes_remover = [
        r'(?i)pr[oó]ximo\s+a?\s*', r'(?i)ao?\s+lado\s+de', r'(?i)em\s+frente\s+a?',
        r'(?i)ponto\s+de\s+refer[\u00ea\u00e9]ncia:?', r'(?i)fundos', r'(?i)bloco\s+\w+',
        r'(?i)apto\.?\s*\d*', r'(?i)andar\s*\d*', r'(?i)lote\s*\d*', r'(?i)quadra\s*\d*'
    ]
    endereco_limpo = endereco
    for padrao in padroes_remover:
        endereco_limpo = re.sub(padrao, '', endereco_limpo)
    endereco_limpo = re.sub(r'\s{2,}', ' ', endereco_limpo).strip(',; ').strip()
    return endereco_limpo

def extrair_linhas_pdf(arquivo):
    linhas = []
    with pdfplumber.open(arquivo) as pdf:
        for pagina in pdf.pages:
            texto = pagina.extract_text()
            if texto:
                for linha in texto.split("\n"):
                    linhas.append(linha.strip())
    return linhas

def extrair_linhas_dados(linhas):
    dados = []
    padrao = re.compile(r'^(\d+)?\s+(A\s*-\s*\d+)\s+(BR\w+)\s+(.+?)\s+([\w\s\u00e3çêó.,/-]+)\s+(\d{8})\s+(Itabuna)', re.IGNORECASE)
    for linha in linhas:
        match = padrao.search(linha)
        if match:
            sequencia, letras, br, endereco, bairro, cep, cidade = match.groups()
            letras_norm = normalizar_letras(letras)
            dados.append({
                'sequencia': sequencia or '',
                'letras': letras.strip(),
                'letras_norm': letras_norm,
                'br': br.strip(),
                'endereco': limpar_endereco(endereco),
                'bairro': bairro.strip(),
                'cep': cep,
                'cidade': cidade.strip(),
                'endereco_formatado': f"{limpar_endereco(endereco)}, {bairro.strip()}, {cidade.strip()}, {cep}"
            })
    return pd.DataFrame(dados)

def geocode_with_retry(geolocator, address, retries=3):
    for i in range(retries):
        try:
            return geolocator.geocode(address, timeout=10)
        except GeocoderTimedOut:
            time.sleep(1)
    return None

def geocodificar_enderecos(df):
    geolocator = Nominatim(user_agent="roteirizador")
    latitudes, longitudes = [], []
    for idx, row in df.iterrows():
        endereco_completo = row['endereco_formatado'] + ", Bahia, Brasil"
        location = geocode_with_retry(geolocator, endereco_completo)
        if location:
            latitudes.append(location.latitude)
            longitudes.append(location.longitude)
        else:
            latitudes.append(None)
            longitudes.append(None)
        if len(df) > 0:
            progress = min(1.0, (idx + 1) / len(df))
            st.progress(progress, text=f"Geocodificando {idx + 1} de {len(df)}")
        time.sleep(1)
    df['latitude'] = latitudes
    df['longitude'] = longitudes
    return df

def criar_matriz_distancias(pontos):
    tamanho = len(pontos)
    matriz = []
    for from_idx in range(tamanho):
        linha = []
        for to_idx in range(tamanho):
            if from_idx == to_idx:
                linha.append(0)
            else:
                distancia = haversine(pontos[from_idx], pontos[to_idx], unit=Unit.KILOMETERS)
                linha.append(int(distancia * 1000))
        matriz.append(linha)
    return matriz

def resolver_rota(matriz):
    tamanho = len(matriz)
    manager = pywrapcp.RoutingIndexManager(tamanho, 1, 0)
    routing = pywrapcp.RoutingModel(manager)

    def distancia_callback(from_index, to_index):
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return matriz[from_node][to_node]

    transit_callback_index = routing.RegisterTransitCallback(distancia_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)
    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    search_parameters.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    solution = routing.SolveWithParameters(search_parameters)

    if solution:
        index = routing.Start(0)
        rota = []
        while not routing.IsEnd(index):
            rota.append(manager.IndexToNode(index))
            index = solution.Value(routing.NextVar(index))
        rota.append(manager.IndexToNode(index))
        return rota
    else:
        return None

def gerar_arquivo_rota(df):
    df['Nome'] = "Pedido " + df['sequencia'] + " - " + df['endereco'] + ", " + df['bairro'] + " [" + df['br'] + "]"
    export_df = df[['Nome', 'latitude', 'longitude']]
    output = io.StringIO()
    export_df.to_csv(output, index=False, encoding='utf-8-sig')
    return output.getvalue().encode("utf-8")

# --- App Streamlit ---
st.set_page_config(page_title="Roteirizador de Entregas Mobile", layout="centered")
st.title("Roteirizador de Entregas Mobile")

uploaded_file = st.file_uploader("Envie o arquivo PDF do romaneio:", type=["pdf"])

if uploaded_file:
    linhas = extrair_linhas_pdf(uploaded_file)
    df_dados = extrair_linhas_dados(linhas)

    if df_dados.empty:
        st.error("Nenhum dado encontrado no PDF com o padrão esperado.")
        st.stop()

    letras_unicas = sorted(df_dados['letras_norm'].unique())
    letras_selecionadas = st.multiselect("Selecione os códigos LETRAS a serem incluídos:", letras_unicas)

    if letras_selecionadas:
        df = df_dados[df_dados['letras_norm'].isin(letras_selecionadas)].copy()

        st.subheader("Dados filtrados para geocodificação")
        st.dataframe(df)

        latitude_manual = st.number_input("Sua latitude", format="%f", value=-14.768865)
        longitude_manual = st.number_input("Sua longitude", format="%f", value=-39.255508)

        if st.button("Gerar rota otimizada"):
            df = geocodificar_enderecos(df)
            total = len(df)
            df = df.dropna(subset=['latitude', 'longitude']).reset_index(drop=True)
            localizados = len(df)
            descartados = total - localizados

            st.success(f"Geocodificação concluída: {localizados} localizados, {descartados} descartados.")

            if latitude_manual != 0.0 and longitude_manual != 0.0:
                origem = (latitude_manual, longitude_manual)
                pontos = [origem] + list(zip(df['latitude'], df['longitude']))
                matriz = criar_matriz_distancias(pontos)
                rota_otima = resolver_rota(matriz)

                if rota_otima:
                    rota_otima = rota_otima[1:]  # remover origem
                    df['ordem'] = -1
                    for ordem, posicao in enumerate(rota_otima):
                        df.loc[posicao - 1, 'ordem'] = ordem
                    df = df.sort_values(by='ordem').reset_index(drop=True)

                    st.success("Rota otimizada gerada!")
                    arquivo_csv = gerar_arquivo_rota(df)
                    st.download_button(
                        label="Baixar rota otimizada (CSV)",
                        data=arquivo_csv,
                        file_name=f"rota_{time.strftime('%Y%m%d_%H%M')}.csv",
                        mime="text/csv"
                    )

                    st.subheader("Visualização da Rota Otimizada")
                    st.dataframe(df[['ordem', 'sequencia', 'endereco', 'bairro', 'br', 'latitude', 'longitude']])

                    st.subheader("Mapa dos pontos geocodificados")
                    st.map(df[['latitude', 'longitude']])
                else:
                    st.error("Não foi possível gerar a rota.")
