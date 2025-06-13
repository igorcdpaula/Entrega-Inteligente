import streamlit as st
import pdfplumber
import re
import pandas as pd
import time
import numpy as np
import io
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut
from ortools.constraint_solver import pywrapcp, routing_enums_pb2
from haversine import haversine, Unit

# --- Funções ---

def extrair_linhas_pdf(arquivo):
    linhas = []
    with pdfplumber.open(arquivo) as pdf:
        for pagina in pdf.pages:
            texto = pagina.extract_text()
            if texto:
                for linha in texto.split("\n"):
                    linhas.append(linha.strip())
    return linhas

def extrair_letras_unicas(linhas):
    letras_set = set()
    for linha in linhas:
        partes = linha.strip().split()
        if len(partes) >= 3:
            letras = partes[1]
            if letras.startswith("A-"):
                letras_set.add(letras)
    return sorted(list(letras_set))

def processar_linhas_filtradas(linhas, letras_selecionadas):
    dados = []
    for linha in linhas:
        partes = linha.strip().split()
        if len(partes) >= 7:
            try:
                sequencia = partes[0]
                letras = partes[1]
                br = partes[2]
                cep = partes[-2]
                cidade = partes[-1]
                bairro = partes[-3]
                endereco = " ".join(partes[3:-3])
                if letras in letras_selecionadas and cidade.lower() == 'itabuna':
                    endereco_formatado = f"{endereco}, {bairro}, {cidade}, {cep}"
                    dados.append({
                        'sequencia': sequencia,
                        'letras': letras,
                        'br': br,
                        'endereco': endereco,
                        'bairro': bairro,
                        'cep': cep,
                        'endereco_formatado': endereco_formatado
                    })
            except Exception:
                continue
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
    for idx, (_, row) in enumerate(df.iterrows()):
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
    processed_data = output.getvalue().encode("utf-8")
    return processed_data

# --- APP ---
st.set_page_config(page_title="Roteirizador de Entregas Mobile", layout="centered")
st.title("Roteirizador de Entregas Mobile")

uploaded_file = st.file_uploader("Envie o arquivo PDF do romaneio:", type=["pdf"])
cidade = st.selectbox("Selecione a cidade:", ["Itabuna", "Ilhéus"])

if uploaded_file and cidade:
    with st.spinner("Lendo o PDF e extraindo códigos LETRAS..."):
        linhas = extrair_linhas_pdf(uploaded_file)
        letras_unicas = extrair_letras_unicas(linhas)

        if not letras_unicas:
            st.error("Não foi possível identificar códigos LETRAS. Verifique o formato do PDF.")
            st.stop()

        letras_selecionadas = st.multiselect("Selecione os códigos LETRAS a serem incluídos:", letras_unicas)

        if not letras_selecionadas:
            st.warning("Selecione pelo menos um código LETRAS para continuar.")
            st.stop()

        df = processar_linhas_filtradas(linhas, letras_selecionadas)

        if df.empty:
            st.error("Nenhum registro encontrado para as LETRAS selecionadas.")
            st.stop()

        st.subheader("Dados filtrados para geocodificação")
        st.dataframe(df)

    latitude_manual = st.number_input("Sua latitude (se não capturado automaticamente)", format="%f", value=-14.768865)
    longitude_manual = st.number_input("Sua longitude (se não capturado automaticamente)", format="%f", value=-39.255508)

    if st.button("Gerar rota otimizada"):
        with st.spinner("Geocodificando endereços..."):
            df = geocodificar_enderecos(df)
            total = len(df)
            df = df.dropna(subset=['latitude', 'longitude']).reset_index(drop=True)
            localizados = len(df)
            descartados = total - localizados

            st.success(f"Geocodificação concluída: {localizados} localizados, {descartados} descartados.")

            end_falhos = df[df['latitude'].isna()][['sequencia', 'endereco_formatado']]
            if not end_falhos.empty:
                st.subheader("Endereços não geocodificados")
                st.dataframe(end_falhos)

            if latitude_manual != 0.0 and longitude_manual != 0.0:
                origem = (latitude_manual, longitude_manual)
            else:
                st.warning("Insira manualmente sua latitude e longitude caso o navegador não capture.")
                origem = None

            if origem:
                pontos = [origem] + list(zip(df['latitude'], df['longitude']))
                matriz = criar_matriz_distancias(pontos)
                rota_otima = resolver_rota(matriz)

                if rota_otima:
                    rota_otima = rota_otima[1:]
                    df['ordem'] = -1
                    for ordem, posicao in enumerate(rota_otima):
                        df.loc[posicao - 1, 'ordem'] = ordem
                    df = df.sort_values(by='ordem').reset_index(drop=True)
                    st.success("Rota otimizada gerada!")

                    arquivo_csv = gerar_arquivo_rota(df)
                    st.download_button(
                        label="Baixar rota otimizada (CSV)",
                        data=arquivo_csv,
                        file_name=f"rota_{cidade.lower()}_{time.strftime('%Y%m%d_%H%M')}.csv",
                        mime="text/csv"
                    )

                    st.subheader("Visualização da Rota Otimizada")
                    st.dataframe(df[['ordem', 'sequencia', 'endereco', 'bairro', 'br', 'latitude', 'longitude']])

                    st.subheader("Mapa dos pontos geocodificados")
                    st.map(df[['latitude', 'longitude']])
                else:
                    st.error("Não foi possível gerar a rota. Tente novamente.")
