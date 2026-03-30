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
    # Gestione typo comuni nei PDF
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
    # Standard: Lun-Gio 8.0h, Ven 4.0h [Basato su orari standard PA/AM]
    ore_standard_base = {0: 8.0, 1: 8.0, 2: 8.0, 3: 8.0, 4: 4.0, 5: 0.0, 6: 0.0}
    nomi_giorni = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
    
    # Parametri per il servizio continuato (diritto al pasto)
    limite_inizio_cont = converti_in_timedelta("14:00")
    limite_fine_cont = converti_in_timedelta("15:30")
    
    dati_righe = []
    giorno_corrente = None
    giorno_sett_idx = None

    with pdfplumber.open(pdf_file) as pdf:
        for pagina in pdf.pages:
            testo = pagina.extract_text()
            if not testo: continue
            
            for linea in testo.split('\n'):
                # 1. Rileva data (es. "02 Lun", "04 Mer") 
                match_data = re.search(r'^(\d{2}\s+[A-Za-z]{3})', linea)
                if match_data:
                    giorno_corrente = match_data.group(1).strip()
                    giorno_sett_idx = ricava_giorno_settimana(giorno_corrente)

                if giorno_corrente is None: continue

                # 2. Estrae orari HH:MM 
                orari = re.findall(r'\b\d{2}:\d{2}\b', linea)
                
                for i in range(0, len(orari) // 2 * 2, 2):
                    ora_in, ora_fi = orari[i], orari[i+1]
                    
                    # Ignora i "Fuori Servizio" 00:00-00:00 
                    if ora_in == "00:00" and ora_fi == "00:00": continue
                    
                    t_in = converti_in_timedelta(ora_in)
                    t_fi = converti_in_timedelta(ora_fi)
                    t_fi_eff = t_fi + timedelta(hours=24) if t_fi <= t_in else t_fi
                    
                    durata = (t_fi_eff - t_in).total_seconds() / 3600
                    
                    # Fallback sicuro per il giorno indice
                    idx_sicuro = giorno_sett_idx if giorno_sett_idx is not None else 0

                    dati_righe.append({
                        "Data": giorno_corrente,
                        "Giorno": nomi_giorni[idx_sicuro],
                        "Giorno_Idx": idx_sicuro,
                        "Inizio": ora_in,
                        "Fine": ora_fi,
                        "Ore_Fatte": durata,
                        "Copre_Mensa": (t_in <= limite_inizio_cont and t_fi_eff >= limite_fine_cont)
                    })
    
    if not dati_righe: return pd.DataFrame(), 0, 0
    
    df_raw = pd.DataFrame(dati_righe)
    
    # 3. Aggregazione Giornaliera
    df_daily = df_raw.groupby('Data').agg({
        'Ore_Fatte': 'sum',
        'Giorno_Idx': 'first',
        'Copre_Mensa': 'any'
    }).reset_index()

    # Calcolo Standard e Straordinario
    def calcola_std(row):
        std = ore_standard_base.get(row['Giorno_Idx'], 0.0)
        # Se copre la fascia mensa (Lun-Gio), lo standard sale di 30 min per recupero pausa
        return (std + 0.5) if (row['Giorno_Idx'] <= 3 and row['Copre_Mensa']) else std

    df_daily['Standard_Rif'] = df_daily.apply(calcola_std, axis=1)
    df_daily['Straord_Dec'] = (df_daily['Ore_Fatte'] - df_daily['Standard_Rif']).clip(lower=0)
    
    # 4. Merge correttivo 
    df_daily = df_daily.rename(columns={'Copre_Mensa': 'Giorno_Continuato', 'Ore_Fatte': 'Totale_Giorno'})
    
    df_output = df_raw.merge(
        df_daily[['Data', 'Standard_Rif', 'Straord_Dec', 'Giorno_Continuato', 'Totale_Giorno']], 
        on='Data', 
        how='left'
    )
    
    # 5. Formattazione Finale
    df_output['Durata Turno'] = df_output['Ore_Fatte'].apply(formatta_hhmm)
    df_output['Totale Giorno'] = df_output['Totale_Giorno'].apply(formatta_hhmm)
    df_output['Straord. Giorno'] = df_output['Straord_Dec'].apply(formatta_hhmm)
    df_output['Standard Rif.'] = df_output['Standard_Rif'].apply(formatta_hhmm)
    df_output['Diritto Pasto'] = df_output['Giorno_Continuato'].map({True: 'SI', False: 'NO'})
    
    colonne = ['Data', 'Giorno', 'Inizio', 'Fine', 'Durata Turno', 'Totale Giorno', 'Standard Rif.', 'Diritto Pasto', 'Straord. Giorno']
    
    ore_totali = df_daily['Totale_Giorno'].sum()
    straord_totali = df_daily['Straord_Dec'].sum()
    
    return df_output[colonne], ore_totali, straord_totali

# --- INTERFACCIA STREAMLIT ---
uploaded_files = st.file_uploader("Carica i PDF Perseo3", type="pdf", accept_multiple_files=True)

if uploaded_files:
    fogli_da_salvare = []
    totale_gen_ore = 0
    totale_gen_straord = 0

    for uploaded_file in uploaded_files:
        df_mese, ore_f, straord_f = analizza_pdf(uploaded_file)
        if not df_mese.empty:
            nome_sheet = re.sub(r'[^\w\s]', '', uploaded_file.name)[:30]
            # Salvo anche i dati del singolo file per il foglio riepilogativo
            fogli_da_salvare.append((nome_sheet, df_mese, ore_f, straord_f))
            
            totale_gen_ore += ore_f
            totale_gen_straord += straord_f

            # Mostra risultati per il singolo file
            with st.expander(f"📄 Dettaglio File: {uploaded_file.name}", expanded=False):
                c1, c2 = st.columns(2)
                c1.metric("Ore Lavorate (Singolo File)", formatta_hhmm(ore_f))
                c2.metric("Straordinario (Singolo File)", formatta_hhmm(straord_f))
                st.dataframe(df_mese, use_container_width=True)

    if fogli_da_salvare:
        st.divider()
        st.subheader("📊 Totale Complessivo (Tutti i file)")
        
        # Mostra i totali generali in basso
        c_tot1, c_tot2 = st.columns(2)
        c_tot1.metric("TOTALE GENERALE ORE", formatta_hhmm(totale_gen_ore))
        c_tot2.metric("TOTALE GENERALE STRAORDINARI", formatta_hhmm(totale_gen_straord))
        
        # Preparazione del file Excel
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            dati_riepilogo = []
            
            for nome, df, ore_f, straord_f in fogli_da_salvare:
                df.to_excel(writer, sheet_name=nome, index=False)
                # Popolo le righe del riepilogo per il file corrente
                dati_riepilogo.append({
                    "Nome File": nome,
                    "Ore Lavorate": formatta_hhmm(ore_f),
                    "Straordinario": formatta_hhmm(straord_f)
                })
            
            # Aggiungo la riga del gran totale al riepilogo Excel
            dati_riepilogo.append({
                "Nome File": "TOTALE GENERALE",
                "Ore Lavorate": formatta_hhmm(totale_gen_ore),
                "Straordinario": formatta_hhmm(totale_gen_straord)
            })

            # Foglio Riepilogo
            pd.DataFrame(dati_riepilogo).to_excel(writer, sheet_name="RIEPILOGO", index=False)

        st.download_button(
            label="📥 Scarica Report Excel Completo",
            data=output.getvalue(),
            file_name=f"Report_Lavoro_Multiplo_{datetime.now().strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
