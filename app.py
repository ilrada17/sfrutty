import streamlit as st
import pdfplumber
import re
import pandas as pd
import io
from datetime import datetime, timedelta

# Configurazione Pagina
st.set_page_config(page_title="Calcolatore Ore Lavoro Aeronautica", layout="wide")
st.title("📊 Calcolatore Ore e Straordinari (Versione Multi-Riga)")
st.write("Analisi avanzata per turni multipli nello stesso giorno.")

def converti_in_timedelta(ora_str):
    if ora_str == "24:00": return timedelta(hours=24)
    try:
        ore, minuti = map(int, ora_str.split(':'))
        return timedelta(hours=ore, minutes=minuti)
    except: return timedelta(0)

def ricava_giorno_settimana(data_str):
    data_str = data_str.lower()
    # Gestione typo comuni nei PDF (es. Sah invece di Sab, Gia invece di Gio)
    if 'lun' in data_str: return 0
    if 'mar' in data_str: return 1
    if 'mer' in data_str: return 2
    if 'gio' in data_str or 'gia' in data_str: return 3
    if 'ven' in data_str: return 4
    if 'sab' in data_str or 'sah' in data_str: return 5
    if 'dom' in data_str: return 6
    
    # Ricerca data formato DD/MM/YYYY
    match = re.search(r'(\d{2}/\d{2}/\d{4})', data_str)
    if match:
        try: return datetime.strptime(match.group(1), "%d/%m/%Y").weekday()
        except: return None
    return None

def formatta_hhmm(ore_decimali):
    if pd.isna(ore_decimali): return "00:00"
    ore = int(ore_decimali)
    minuti = int(round((ore_decimali - ore) * 60))
    return f"{ore:02d}:{minuti:02d}"

def analizza_pdf(pdf_file):
    # Standard: Lun-Gio 8.0h, Ven 4.0h
    ore_standard_base = {0: 8.0, 1: 8.0, 2: 8.0, 3: 8.0, 4: 4.0, 5: 0.0, 6: 0.0}
    nomi_giorni = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
    
    # Parametri per il servizio continuato (mensa)
    limite_inizio_cont = converti_in_timedelta("14:00")
    limite_fine_cont = converti_in_timedelta("15:30")
    
    dati_righe = []
    
    # VARIABILI DI MEMORIA (Persistono tra le righe)
    giorno_corrente = None
    giorno_sett_idx = None

    with pdfplumber.open(pdf_file) as pdf:
        for pagina in pdf.pages:
            testo = pagina.extract_text()
            if not testo: continue
            
            for linea in testo.split('\n'):
                # 1. Tenta di rilevare una NUOVA data (es. "04 Mer" o "04/02/2026")
                match_data = re.search(r'^(\d{2}\s+[A-Za-z]{3}|\d{2}/\d{2}/\d{4})', linea)
                
                if match_data:
                    giorno_corrente = match_data.group(1).strip()
                    giorno_sett_idx = ricava_giorno_settimana(giorno_corrente)

                # Se non abbiamo ancora trovato la prima data utile del file, salta
                if giorno_corrente is None or giorno_sett_idx is None:
                    continue

                # 2. Trova tutti gli orari HH:MM nella riga
                orari = re.findall(r'\b\d{2}:\d{2}\b', linea)
                
                # Elabora le coppie Inizio/Fine (anche più coppie per riga se presenti)
                for i in range(0, len(orari) // 2 * 2, 2):
                    ora_in, ora_fi = orari[i], orari[i+1]
                    
                    # Salta righe di "Fuori Servizio" con 00:00-00:00
                    if ora_in == "00:00" and ora_fi == "00:00":
                        continue
                    
                    t_in = converti_in_timedelta(ora_in)
                    t_fi = converti_in_timedelta(ora_fi)
                    
                    # Gestione turno notturno che scavalca la mezzanotte
                    t_fi_effettiva = t_fi + timedelta(hours=24) if t_fi <= t_in else t_fi
                    durata_ore = (t_fi_effettiva - t_in).total_seconds() / 3600
                    
                    # Verifica se questo specifico segmento copre la pausa pranzo
                    segmento_continuato = (t_in <= limite_inizio_cont) and (t_fi_effettiva >= limite_fine_cont)

                    dati_righe.append({
                        "Data_Originale": giorno_corrente,
                        "Giorno": nomi_giorni[giorno_sett_idx],
                        "Giorno_Idx": giorno_sett_idx,
                        "Inizio": ora_in,
                        "Fine": ora_fi,
                        "Ore_Fatte": durata_ore,
                        "Segmento_Cont": segmento_continuato
                    })
    
    if not dati_righe: return pd.DataFrame(), 0, 0
    
    df_raw = pd.DataFrame(dati_righe)
    
    # 3. RAGGRUPPAMENTO PER GIORNO
    # Sommiamo tutte le ore fatte nello stesso giorno e verifichiamo il continuato totale
    df_daily = df_raw.groupby('Data_Originale').agg({
        'Ore_Fatte': 'sum',
        'Giorno_Idx': 'first',
        'Giorno': 'first',
        'Segmento_Cont': 'any'
    }).reset_index()

    def calcola_standard(row):
        # Orario base
        base = ore_standard_base[row['Giorno_Idx']]
        # Se ha fatto continuato (Segmento_Cont è True) e non è venerdì/sabato/domenica, aggiungi 30min
        if row['Giorno_Idx'] <= 3 and row['Segmento_Cont']:
            return base + 0.5
        return base

    df_daily['Standard_Rif'] = df_daily.apply(calcola_standard, axis=1)
    df_daily['Straordinario_Dec'] = (df_daily['Ore_Fatte'] - df_daily['Standard_Rif']).clip(lower=0)
    
    # Prepara DataFrame per output leggibile
    df_output = df_raw.merge(
        df_daily[['Data_Originale', 'Standard_Rif', 'Straordinario_Dec', 'Segmento_Cont', 'Ore_Fatte']], 
        on='Data_Originale', 
        how='left'
    )
    
    # Formattazione per visualizzazione
    df_output['Durata Turno'] = df_output['Ore_Fatte_x'].apply(formatta_hhmm)
    df_output['Totale Giorno'] = df_output['Ore_Fatte_y'].apply(formatta_hhmm)
    df_output['Straord. Giorno'] = df_output['Straordinario_Dec'].apply(formatta_hhmm)
    df_output['Standard Applicato'] = df_output['Standard_Rif'].apply(formatta_hhmm)
    df_output['Cont.'] = df_output['Segmento_Cont'].map({True: 'SI', False: 'NO'})
    
    colonne_finali = [
        'Data_Originale', 'Giorno', 'Inizio', 'Fine', 
        'Durata Turno', 'Totale Giorno', 'Standard Applicato', 'Cont.', 'Straord. Giorno'
    ]
    
    return df_output[colonne_finali], df_daily['Ore_Fatte'].sum(), df_daily['Straordinario_Dec'].sum()

# --- INTERFACCIA STREAMLIT ---
uploaded_files = st.file_uploader("Carica i PDF Perseo3", type="pdf", accept_multiple_files=True)

if uploaded_files:
    fogli_da_salvare = []
    totale_gen_ore = 0
    totale_gen_straord = 0

    for uploaded_file in uploaded_files:
        df_mese, ore_f, straord_f = analizza_pdf(uploaded_file)
        if not df_mese.empty:
            # Pulizia nome file per il foglio Excel
            nome_sheet = re.sub(r'[^\w\s]', '', uploaded_file.name)[:30]
            fogli_da_salvare.append((nome_sheet, df_mese))
            totale_gen_ore += ore_f
            totale_gen_straord += straord_f

    if fogli_da_salvare:
        st.divider()
        c1, c2 = st.columns(2)
        c1.metric("Totale Ore Lavorate", formatta_hhmm(totale_gen_ore))
        c2.metric("Totale Straordinario", formatta_hhmm(totale_gen_straord))
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            for nome, df in fogli_da_salvare:
                df.to_excel(writer, sheet_name=nome, index=False)
                with st.expander(f"Dettaglio: {nome}"):
                    st.dataframe(df, use_container_width=True)
            
            # Foglio Riepilogo
            pd.DataFrame({
                "Descrizione": ["Totale Ore", "Totale Straordinario"],
                "HH:MM": [formatta_hhmm(totale_gen_ore), formatta_hhmm(totale_gen_straord)]
            }).to_excel(writer, sheet_name="RIEPILOGO", index=False)

        st.download_button(
            label="📥 Scarica Report Excel",
            data=output.getvalue(),
            file_name=f"Report_Lavoro_{datetime.now().strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
