import streamlit as st
import pdfplumber
import re
import pandas as pd
import io
from datetime import datetime, timedelta

# Configurazione Pagina
st.set_page_config(page_title="Calcolatore Ore Lavoro", layout="wide")
st.title("📊 Calcolatore Ore e Straordinari")
st.write("Carica i tuoi PDF e scarica il report Excel aggiornato.")

def converti_in_timedelta(ora_str):
    if ora_str == "24:00": return timedelta(hours=24)
    try:
        ore, minuti = map(int, ora_str.split(':'))
        return timedelta(hours=ore, minutes=minuti)
    except: return timedelta(0)

def ricava_giorno_settimana(data_str):
    data_str = data_str.lower()
    if 'lun' in data_str: return 0
    if 'mar' in data_str: return 1
    if 'mer' in data_str: return 2
    if 'gio' in data_str or 'gia' in data_str: return 3
    if 'ven' in data_str: return 4
    if 'sab' in data_str or 'sah' in data_str: return 5
    if 'dom' in data_str: return 6
    match = re.search(r'(\d{2}/\d{2}/\d{4})', data_str)
    if match: return datetime.strptime(match.group(1), "%d/%m/%Y").weekday()
    return None

def formatta_hhmm(ore_decimali):
    ore = int(ore_decimali)
    minuti = int(round((ore_decimali - ore) * 60))
    return f"{ore:02d}:{minuti:02d}"

def analizza_pdf(pdf_file):
    # Standard base: Lun-Gio 8h, Ven 4h
    ore_standard_base = {0: 8.0, 1: 8.0, 2: 8.0, 3: 8.0, 4: 4.0, 5: 0.0, 6: 0.0}
    nomi_giorni = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
    limite_inizio_cont = converti_in_timedelta("14:00")
    limite_fine_cont = converti_in_timedelta("15:30")
    
    dati_righe = []
    giorno_corrente = "Sconosciuto"
    giorno_sett_idx = None

    with pdfplumber.open(pdf_file) as pdf:
        for pagina in pdf.pages:
            testo = pagina.extract_text()
            if not testo: continue
            for linea in testo.split('\n'):
                # Cerchiamo gli orari (es. 08:00)
                orari = re.findall(r'\b\d{2}:\d{2}\b', linea)
                if len(orari) >= 2:
                    ora_in, ora_fi = orari[0], orari[1]
                    if ora_in == "00:00" and ora_fi == "00:00": continue
                    
                    # Cerchiamo la data nella riga (formato '01 Mer' o '01/10/2025')
                    match_data = re.search(r'(\d{2}\s+[A-Za-z]{3}|\d{2}/\d{2}/\d{4})', linea)
                    if match_data:
                        giorno_corrente = match_data.group(1).strip()
                        giorno_sett_idx = ricava_giorno_settimana(giorno_corrente)
                    
                    if giorno_sett_idx is None: continue

                    t_in = converti_in_timedelta(ora_in)
                    t_fi = converti_in_timedelta(ora_fi)
                    t_fi_effettiva = t_fi + timedelta(hours=24) if t_fi <= t_in else t_fi
                    
                    durata_ore = (t_fi_effettiva - t_in).total_seconds() / 3600
                    # Verifica se il turno copre la fascia 14:00 - 15:30
                    is_continuato = (t_in <= limite_inizio_cont) and (t_fi_effettiva >= limite_fine_cont)

                    dati_righe.append({
                        "Data": giorno_corrente,
                        "Giorno": nomi_giorni[giorno_sett_idx],
                        "Giorno_Idx": giorno_sett_idx,
                        "Inizio": ora_in,
                        "Fine": ora_fi,
                        "Ore_Fatte": durata_ore,
                        "Is_Cont": is_continuato
                    })
    
    if not dati_righe: return pd.DataFrame()
    
    df = pd.DataFrame(dati_righe)
    
    # Calcolo dei totali giornalieri (sommiamo i turni se ce ne sono più di uno al giorno)
    df_daily = df.groupby('Data').agg({
        'Ore_Fatte': 'sum',
        'Giorno_Idx': 'first',
        'Is_Cont': 'any'
    }).reset_index()
    
    # Rinominiamo per evitare conflitti nel merge
    df_daily = df_daily.rename(columns={'Ore_Fatte': 'Ore_Tot_Giorno', 'Is_Cont': 'Giorno_Continuato'})

    def calcola_standard(row):
        base = ore_standard_base[row['Giorno_Idx']]
        # Regola 30 min extra per orario continuato Lun-Gio
        if row['Giorno_Idx'] <= 3 and row['Giorno_Continuato']:
            return base + 0.5
        return base

    df_daily['Standard_Applicato'] = df_daily.apply(calcola_standard, axis=1)
    df_daily['Straordinario_Dec'] = (df_daily['Ore_Tot_Giorno'] - df_daily['Standard_Applicato']).clip(lower=0)
    
    # Uniamo i risultati giornalieri alle righe dei singoli turni
    df_output = df.merge(df_daily[['Data', 'Standard_Applicato', 'Straordinario_Dec', 'Giorno_Continuato']], on='Data', how='left')
    
    # Formattazione per Excel
    df_output['Durata Turno'] = df_output['Ore_Fatte'].apply(formatta_hhmm)
    df_output['Straord. Giorno'] = df_output['Straordinario_Dec'].apply(formatta_hhmm)
    df_output['Standard Rif.'] = df_output['Standard_Applicato'].apply(formatta_hhmm)
    df_output['Orario Continuato'] = df_output['Giorno_Continuato'].map({True: 'SI', False: 'NO'})
    
    # Selezione finale colonne garantendo che esistano
    colonne_finali = ['Data', 'Giorno', 'Inizio', 'Fine', 'Durata Turno', 'Orario Continuato', 'Standard Rif.', 'Straord. Giorno']
    return df_output[colonne_finali]

# --- INTERFACCIA STREAMLIT ---
uploaded_files = st.file_uploader("Scegli i file PDF", type="pdf", accept_multiple_files=True)

if uploaded_files:
    fogli_da_salvare = []

    for uploaded_file in uploaded_files:
        with st.spinner(f"Analisi di {uploaded_file.name}..."):
            df_mese = analizza_pdf(uploaded_file)
            if not df_mese.empty:
                fogli_da_salvare.append((uploaded_file.name[:31], df_mese))
            else:
                st.warning(f"⚠️ Nessun dato trovato in {uploaded_file.name}")

    if fogli_da_salvare:
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            for nome_foglio, df in fogli_da_salvare:
                df.to_excel(writer, sheet_name=nome_foglio, index=False)
                st.subheader(f"✅ Anteprima: {nome_foglio}")
                st.dataframe(df)
        
        st.success("Tutti i file sono stati elaborati!")
        st.download_button(
            label="📥 Scarica Report Excel",
            data=output.getvalue(),
            file_name="Report_Ore_Lavoro.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    else:
        st.error("❌ Non è stato possibile generare l'Excel perché non sono stati estratti dati validi dai PDF.")
