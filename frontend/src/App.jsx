import React, { useState, useEffect, useRef, useCallback } from 'react';
import * as LWCharts from 'lightweight-charts';
import { AreaChart, Area, BarChart, Bar, Cell, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';
import { Play, TrendingUp, BarChart2, Activity, Zap, Save, Radio, ShieldCheck, RefreshCw, XCircle, Wifi } from 'lucide-react';

const API = 'http://127.0.0.1:8000';
const STORAGE_KEY = 'spy_backtest_config';
const PRESETS_KEY = 'spy_backtest_presets';
const TRADE_PRESETS_KEY = 'spy_trading_presets';

function loadTradePresets() {
  try { const s = localStorage.getItem(TRADE_PRESETS_KEY); if (s) return JSON.parse(s); } catch {} return {};
}

const DEFAULT_CONFIG = {
  ticker: 'SPY', years_history: 2, capital_allocation: 10000.0,
  use_dynamic_sizing: false, risk_percent: 5.0, max_trade_cap: 0,
  contracts_per_trade: 1, spread_cost_target: 250.0,
  use_targeted_spread: false, target_spread_pct: 2.0, max_allocation_cap: 2500.0,
  realism_factor: 1.15,
  strategy_id: 'consecutive_days', strategy_type: 'bull_call',
  topology: 'vertical_spread', direction: 'bull', strike_width: 5,
  entry_red_days: 2, exit_green_days: 2,
  combo_sma1: 3, combo_sma2: 8, combo_sma3: 10,
  combo_ema1: 5, combo_ema2: 3, combo_max_bars: 10, combo_max_profit_closes: 5,
  take_profit_pct: 0, trailing_stop_pct: 0, target_dte: 14, stop_loss_pct: 50,
  commission_per_contract: 0.65,
  use_rsi_filter: true, rsi_threshold: 30,
  use_ema_filter: true, ema_length: 10,
  use_sma200_filter: false, use_volume_filter: false,
  use_mark_to_market: true, enable_mc_histogram: true,
  enable_walk_forward: false, walk_forward_windows: 4,
  use_vix_filter: false, vix_min: 15, vix_max: 35,
  use_regime_filter: false, regime_allowed: 'all',
};

const BUILT_IN_PRESETS = {
  'Conservative': { ...DEFAULT_CONFIG, entry_red_days: 3, target_dte: 21, stop_loss_pct: 30, spread_cost_target: 150, use_rsi_filter: true, rsi_threshold: 25, use_regime_filter: true, regime_allowed: 'bull' },
  'Aggressive':   { ...DEFAULT_CONFIG, entry_red_days: 1, target_dte: 7,  stop_loss_pct: 75, spread_cost_target: 400, use_rsi_filter: false, use_ema_filter: false, contracts_per_trade: 3 },
  'Post-Crash':   { ...DEFAULT_CONFIG, entry_red_days: 4, target_dte: 30, stop_loss_pct: 50, use_vix_filter: true, vix_min: 25, vix_max: 60, use_rsi_filter: true, rsi_threshold: 20 },
  'Low-Vol Scalp':{ ...DEFAULT_CONFIG, entry_red_days: 2, target_dte: 7,  stop_loss_pct: 40, spread_cost_target: 100, use_vix_filter: true, vix_min: 10, vix_max: 20 },
  'Bear Market':  { ...DEFAULT_CONFIG, strategy_type: 'bear_put', direction: 'bear', entry_red_days: 2, target_dte: 14, use_regime_filter: true, regime_allowed: 'bear' },
};

function loadConfig() {
  try { const s = localStorage.getItem(STORAGE_KEY); if (s) return { ...DEFAULT_CONFIG, ...JSON.parse(s) }; } catch {} return DEFAULT_CONFIG;
}
function loadPresets() {
  try { const s = localStorage.getItem(PRESETS_KEY); if (s) return JSON.parse(s); } catch {} return {};
}

function Inp({ name, value, onChange, ...rest }) {
  return <input type="number" name={name} value={value} onChange={onChange} style={{ width:'100%',background:'var(--bg-card)',border:'1px solid var(--border)',color:'#fff',padding:'8px 12px',borderRadius:8,fontFamily:'inherit',fontSize:'0.85rem' }} {...rest}/>;
}
function Section({ label, children }) {
  return <div style={{marginBottom:18}}><p style={{fontSize:'0.72rem',fontWeight:600,color:'#6b46c1',textTransform:'uppercase',letterSpacing:1,marginBottom:8}}>{label}</p>{children}</div>;
}
function Field({ label, children, style }) {
  return <div className="form-group" style={style}><label style={{fontSize:'0.78rem',color:'#8b8b9d',display:'block',marginBottom:5}}>{label}</label>{children}</div>;
}
function Toggle({ name, label, checked, onChange }) {
  return <div className="toggle-group"><label style={{color:checked?'#f0f0f5':'#8b8b9d',transition:'color .2s'}}>{label}</label><label className="switch"><input type="checkbox" name={name} checked={checked} onChange={onChange}/><span className="slider"/></label></div>;
}
function MetricCard({ title, value, color }) {
  return <div className="metric-card"><span className="metric-label">{title}</span><span className="metric-value" style={{color:color||'#f0f0f5'}}>{value}</span></div>;
}
function MsgBox({ msg }) {
  if (!msg) return null;
  const isErr = msg.startsWith('Error') || msg.startsWith('error');
  return <div style={{marginTop:12,padding:'8px 12px',borderRadius:8,background:isErr?'rgba(245,101,101,0.1)':'rgba(72,187,120,0.1)',border:`1px solid ${isErr?'#f56565':'#48bb78'}`,fontSize:'0.8rem',color:isErr?'#f56565':'#48bb78'}}>{msg}</div>;
}

/* ── SPY Sparkline ───────────────────────────────────────────── */
function SpySparkline({ data, current, change, pct }) {
  if (!data || data.length < 2) return <span style={{color:'#8b8b9d',fontSize:'0.75rem'}}>SPY –</span>;
  const closes = data.map(d => d.close);
  const mn = Math.min(...closes), mx = Math.max(...closes), range = mx - mn || 1;
  const W = 100, H = 30;
  const pts = closes.map((c, i) => `${(i / (closes.length - 1)) * W},${H - ((c - mn) / range) * H}`).join(' ');
  const col = change >= 0 ? '#48bb78' : '#f56565';
  return (
    <div style={{display:'flex',alignItems:'center',gap:8}}>
      <svg width={W} height={H}><polyline points={pts} fill="none" stroke={col} strokeWidth={1.5}/></svg>
      <div>
        <div style={{fontSize:'0.85rem',fontWeight:700,color:'#f0f0f5'}}>${current}</div>
        <div style={{fontSize:'0.7rem',color:col}}>{change>=0?'+':''}{change} ({pct>=0?'+':''}{pct}%)</div>
      </div>
    </div>
  );
}

/* ── Account HUD ─────────────────────────────────────────────── */
function AccountHUD({ account, mode, connStatus, spyData, onRefresh }) {
  const col = connStatus==='online'?'#48bb78':connStatus==='connecting'?'#ecc94b':'#f56565';
  const label = connStatus==='online'?'LIVE':connStatus==='connecting'?'CONNECTING':'OFFLINE';
  return (
    <div style={{background:'var(--bg-card)',borderRadius:12,border:`1px solid ${account?col:'var(--border)'}`,padding:'12px 20px',display:'flex',alignItems:'center',gap:20,flexWrap:'wrap',marginBottom:0}}>
      <div style={{display:'flex',alignItems:'center',gap:6,minWidth:120}}>
        <div style={{width:9,height:9,borderRadius:'50%',background:col,boxShadow:`0 0 6px ${col}`,flexShrink:0}}/>
        <span style={{fontSize:'0.72rem',fontWeight:700,color:col,textTransform:'uppercase',letterSpacing:0.5}}>
          {mode==='paper'?'Alpaca Paper':'IBKR Live'} · {label}
        </span>
      </div>
      {account ? (
        <div style={{display:'flex',gap:20,fontSize:'0.8rem',flex:1,flexWrap:'wrap',alignItems:'center'}}>
          <span style={{color:'#8b8b9d'}}>Equity <strong style={{color:'#f0f0f5'}}>${Number(account.equity||0).toLocaleString(undefined,{maximumFractionDigits:0})}</strong></span>
          <span style={{color:'#8b8b9d'}}>BP <strong style={{color:'#48bb78'}}>${Number(account.buying_power||0).toLocaleString(undefined,{maximumFractionDigits:0})}</strong></span>
          {account.cash!==undefined&&<span style={{color:'#8b8b9d'}}>Cash <strong style={{color:'#f0f0f5'}}>${Number(account.cash||0).toLocaleString(undefined,{maximumFractionDigits:0})}</strong></span>}
          {account.daily_pnl!==undefined&&<span style={{color:'#8b8b9d'}}>Day P&L <strong style={{color:Number(account.daily_pnl)>=0?'#48bb78':'#f56565'}}>{Number(account.daily_pnl)>=0?'+':''}${Number(account.daily_pnl||0).toFixed(2)}</strong></span>}
          {account.unrealized_pnl!==undefined&&<span style={{color:'#8b8b9d'}}>Unrealized <strong style={{color:Number(account.unrealized_pnl)>=0?'#48bb78':'#f56565'}}>{Number(account.unrealized_pnl)>=0?'+':''}${Number(account.unrealized_pnl||0).toFixed(2)}</strong></span>}
        </div>
      ) : (
        <span style={{fontSize:'0.8rem',color:'#8b8b9d',flex:1}}>Not connected — enter credentials below</span>
      )}
      {spyData&&spyData.current>0&&<SpySparkline data={spyData.data} current={spyData.current} change={spyData.change} pct={spyData.change_pct}/>}
      <button onClick={onRefresh} style={{background:'none',border:'1px solid var(--border)',borderRadius:8,color:'#8b8b9d',cursor:'pointer',padding:'5px 10px',display:'flex',alignItems:'center',gap:4,fontSize:'0.72rem',whiteSpace:'nowrap'}}><RefreshCw size={11}/>Refresh</button>
    </div>
  );
}

/* ── Trading Presets Bar ─────────────────────────────────────── */
function TradingPresetsBar({ presets, onLoad, onSave, onDelete }) {
  const [showSave, setShowSave] = useState(false);
  const [saveName, setSaveName] = useState('');
  const doSave = () => { if (!saveName.trim()) return; onSave(saveName.trim()); setSaveName(''); setShowSave(false); };
  return (
    <div style={{background:'var(--bg-card)',borderRadius:12,border:'1px solid var(--border)',padding:'10px 16px',display:'flex',alignItems:'center',gap:10,flexWrap:'wrap'}}>
      <span style={{fontSize:'0.72rem',fontWeight:600,color:'#6b46c1',textTransform:'uppercase',letterSpacing:1,whiteSpace:'nowrap'}}>Trading Presets</span>
      <div style={{display:'flex',gap:5,flex:1,flexWrap:'wrap'}}>
        {Object.keys(presets).length===0&&<span style={{fontSize:'0.75rem',color:'#8b8b9d',fontStyle:'italic'}}>No saved presets</span>}
        {Object.keys(presets).map(name=>(
          <div key={name} style={{display:'flex'}}>
            <button onClick={()=>onLoad(presets[name])} style={{padding:'4px 10px',background:'rgba(107,70,193,0.15)',border:'1px solid rgba(107,70,193,0.35)',borderRight:'none',borderRadius:'6px 0 0 6px',color:'#a78bfa',fontSize:'0.75rem',cursor:'pointer',fontWeight:600}}>{name}</button>
            <button onClick={()=>onDelete(name)} title="Delete" style={{padding:'4px 8px',background:'rgba(245,101,101,0.08)',border:'1px solid rgba(107,70,193,0.35)',borderLeft:'none',borderRadius:'0 6px 6px 0',color:'#f56565',fontSize:'0.75rem',cursor:'pointer',lineHeight:1}}>×</button>
          </div>
        ))}
      </div>
      {showSave?(
        <div style={{display:'flex',gap:6,alignItems:'center'}}>
          <input value={saveName} onChange={e=>setSaveName(e.target.value)} onKeyDown={e=>e.key==='Enter'&&doSave()} placeholder="Preset name…" autoFocus style={{width:140,background:'var(--bg-dark)',border:'1px solid var(--border)',color:'#fff',padding:'5px 10px',borderRadius:6,fontSize:'0.8rem',fontFamily:'inherit'}}/>
          <button onClick={doSave} style={{padding:'5px 12px',background:'rgba(107,70,193,0.3)',border:'1px solid var(--accent)',borderRadius:6,color:'#a78bfa',cursor:'pointer',fontSize:'0.8rem',fontWeight:700}}>Save</button>
          <button onClick={()=>setShowSave(false)} style={{padding:'5px 8px',background:'none',border:'1px solid var(--border)',borderRadius:6,color:'#8b8b9d',cursor:'pointer',fontSize:'0.8rem'}}>✕</button>
        </div>
      ):(
        <button onClick={()=>setShowSave(true)} style={{padding:'4px 12px',background:'none',border:'1px solid var(--border)',borderRadius:6,color:'#8b8b9d',cursor:'pointer',fontSize:'0.75rem',display:'flex',alignItems:'center',gap:4,whiteSpace:'nowrap'}}><Save size={12}/>Save Current</button>
      )}
    </div>
  );
}

/* ── Paper Trading Panel ─────────────────────────────────────── */
const SEL = {background:'var(--bg-dark)',border:'1px solid var(--border)',color:'#fff',padding:'8px 12px',borderRadius:8,fontFamily:'inherit',fontSize:'0.85rem',width:'100%'};
const INP = {background:'var(--bg-dark)',border:'1px solid var(--border)',color:'#fff',padding:'8px 12px',borderRadius:8,fontFamily:'inherit',fontSize:'0.85rem'};

function SignalBadge({ signal }) {
  if (!signal) return null;
  return (
    <div style={{padding:'12px 16px',borderRadius:8,background:signal.signal?'rgba(72,187,120,0.08)':'rgba(255,255,255,0.03)',border:`1px solid ${signal.signal?'#48bb78':'var(--border)'}`}}>
      <div style={{display:'flex',gap:16,alignItems:'center',flexWrap:'wrap'}}>
        <div style={{display:'flex',alignItems:'center',gap:8}}>
          <div style={{width:12,height:12,borderRadius:'50%',background:signal.signal?'#48bb78':'#8b8b9d',boxShadow:signal.signal?'0 0 8px #48bb78':'none'}}/>
          <span style={{fontWeight:700,fontSize:'1rem',color:signal.signal?'#48bb78':'#8b8b9d'}}>{signal.signal?'SIGNAL FIRING':'NO SIGNAL'}</span>
        </div>
        {signal.price&&<span style={{fontSize:'0.8rem',color:'#8b8b9d'}}>Price <strong style={{color:'#f0f0f5'}}>${signal.price}</strong></span>}
        {signal.rsi&&<span style={{fontSize:'0.8rem',color:'#8b8b9d'}}>RSI <strong style={{color:'#f0f0f5'}}>{signal.rsi}</strong></span>}
        {signal.rsi_ok!==undefined&&<span style={{fontSize:'0.75rem',color:signal.rsi_ok?'#48bb78':'#f56565'}}>RSI {signal.rsi_ok?'✓':'✗'}</span>}
        {signal.ema_ok!==undefined&&<span style={{fontSize:'0.75rem',color:signal.ema_ok?'#48bb78':'#f56565'}}>EMA {signal.ema_ok?'✓':'✗'}</span>}
        {signal.timestamp&&<span style={{fontSize:'0.7rem',color:'#8b8b9d'}}>{new Date(signal.timestamp).toLocaleTimeString()}</span>}
      </div>
    </div>
  );
}

function PaperPanel({ paperKey, setPaperKey, paperSecret, setPaperSecret, paperAccount, paperConnecting, connectPaper, paperPositions, paperOrders, paperSignal, scanLog, paperScanning, paperAutoExec, setPaperAutoExec, scanTimingMode, setScanTimingMode, scanTimingValue, setScanTimingValue, toggleScanning, manualScan, killSwitch, msg, refreshData, spyData, tradePresets, onSavePreset, onLoadPreset, onDeletePreset }) {
  const [tab, setTab] = useState('positions');
  const connStatus = paperAccount ? 'online' : paperConnecting ? 'connecting' : 'offline';

  const timingLabel = {interval:'Every N sec',after_open:'N min after open',before_close:'N min before close',on_open:'At market open',on_close:'At market close'};

  return (
    <div style={{display:'flex',flexDirection:'column',gap:14}}>
      <AccountHUD account={paperAccount} mode="paper" connStatus={connStatus} spyData={spyData} onRefresh={refreshData}/>

      <TradingPresetsBar presets={tradePresets} onLoad={onLoadPreset} onSave={onSavePreset} onDelete={onDeletePreset}/>

      {/* Connection */}
      <div style={{background:'var(--bg-card)',borderRadius:12,border:'1px solid var(--border)',padding:20}}>
        <h3 style={{marginBottom:14,fontSize:'0.9rem',color:'#8b8b9d',display:'flex',alignItems:'center',gap:8}}><Radio size={16}/>Alpaca Paper Trading</h3>
        <div style={{display:'grid',gridTemplateColumns:'1fr 1fr auto',gap:12,alignItems:'flex-end'}}>
          <Field label="API Key"><input type="password" value={paperKey} onChange={e=>setPaperKey(e.target.value)} placeholder="Alpaca API Key" style={INP}/></Field>
          <Field label="Secret Key"><input type="password" value={paperSecret} onChange={e=>setPaperSecret(e.target.value)} placeholder="Alpaca Secret" style={INP}/></Field>
          <button className="btn-primary" onClick={connectPaper} disabled={paperConnecting} style={{width:'auto',padding:'8px 20px',marginTop:0}}>
            <Wifi size={14}/>{paperConnecting?'Connecting…':paperAccount?'Reconnect':'Connect'}
          </button>
        </div>
        <MsgBox msg={msg}/>
      </div>

      {/* Scanner */}
      <div style={{background:'var(--bg-card)',borderRadius:12,border:'1px solid var(--border)',padding:20}}>
        <h3 style={{marginBottom:14,fontSize:'0.9rem',color:'#8b8b9d',display:'flex',alignItems:'center',gap:8}}><Activity size={16}/>Scanner</h3>
        <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:12,marginBottom:12}}>
          <Field label="Scan Timing">
            <select value={scanTimingMode} onChange={e=>setScanTimingMode(e.target.value)} style={SEL}>
              {Object.entries(timingLabel).map(([k,v])=><option key={k} value={k}>{v}</option>)}
            </select>
          </Field>
          {['interval','after_open','before_close'].includes(scanTimingMode)&&(
            <Field label={scanTimingMode==='interval'?'Interval (sec)':'Offset (min)'}>
              <input type="number" value={scanTimingValue} onChange={e=>setScanTimingValue(Number(e.target.value))} min={1} style={{...INP,width:'100%'}}/>
            </Field>
          )}
        </div>
        <div style={{display:'flex',gap:10,flexWrap:'wrap',alignItems:'center'}}>
          <div style={{display:'flex',alignItems:'center',gap:8}}>
            <label style={{fontSize:'0.78rem',color:'#8b8b9d'}}>Auto-Execute</label>
            <label className="switch"><input type="checkbox" checked={paperAutoExec} onChange={e=>setPaperAutoExec(e.target.checked)}/><span className="slider"/></label>
          </div>
          <button onClick={manualScan} className="btn-secondary" style={{padding:'8px 14px',display:'flex',alignItems:'center',gap:6}}><RefreshCw size={14}/>Scan Now</button>
          <button onClick={toggleScanning} style={{padding:'8px 18px',borderRadius:8,border:`1px solid ${paperScanning?'#f56565':'var(--accent)'}`,background:paperScanning?'rgba(245,101,101,0.15)':'rgba(107,70,193,0.2)',color:paperScanning?'#f56565':'#a78bfa',cursor:'pointer',fontWeight:700,fontSize:'0.8rem',display:'flex',alignItems:'center',gap:6}}>
            <Radio size={14}/>{paperScanning?'Stop Scan':'Start Scan'}
          </button>
          <button onClick={killSwitch} style={{padding:'8px 14px',background:'rgba(245,101,101,0.15)',border:'1px solid #f56565',borderRadius:8,color:'#f56565',cursor:'pointer',display:'flex',alignItems:'center',gap:6,fontSize:'0.8rem',fontWeight:700}}>
            <XCircle size={14}/>KILL
          </button>
        </div>
        {paperScanning&&<div style={{marginTop:8,fontSize:'0.72rem',color:'#8b8b9d'}}>Scanning · {timingLabel[scanTimingMode]}{['interval','after_open','before_close'].includes(scanTimingMode)?` (${scanTimingValue})`:''}</div>}
        <div style={{marginTop:12}}><SignalBadge signal={paperSignal}/></div>
      </div>

      {/* Data */}
      <div>
        <div style={{display:'flex',gap:4,borderBottom:'1px solid var(--border)'}}>
          {['positions','open orders','all orders','scan log'].map(t=>(
            <button key={t} onClick={()=>setTab(t)} style={{padding:'8px 16px',background:'none',border:'none',borderBottom:tab===t?'2px solid var(--accent)':'2px solid transparent',color:tab===t?'#a78bfa':'#8b8b9d',fontWeight:600,fontSize:'0.8rem',cursor:'pointer',textTransform:'capitalize'}}>{t}</button>
          ))}
          <button onClick={refreshData} style={{marginLeft:'auto',background:'none',border:'none',color:'#8b8b9d',cursor:'pointer',padding:'8px 12px',display:'flex',alignItems:'center'}}><RefreshCw size={14}/></button>
        </div>
        {tab==='positions'&&(
          <div className="table-container"><table>
            <thead><tr><th>Symbol</th><th>Qty</th><th>Side</th><th>Avg Price</th><th>Current</th><th>Market Val</th><th>Unrealized P&L</th></tr></thead>
            <tbody>
              {paperPositions.length===0&&<tr><td colSpan={7} style={{textAlign:'center',color:'#8b8b9d',padding:24}}>No open positions</td></tr>}
              {paperPositions.map((p,i)=>(
                <tr key={i}>
                  <td style={{fontWeight:600}}>{p.symbol}</td><td>{p.qty}</td>
                  <td><span className={`badge ${p.side==='long'?'win':'loss'}`}>{p.side}</span></td>
                  <td>${Number(p.avg_price).toFixed(2)}</td><td>${Number(p.current_price).toFixed(2)}</td>
                  <td>${Number(p.market_value).toFixed(2)}</td>
                  <td style={{color:p.unrealized_pl>=0?'#48bb78':'#f56565',fontWeight:600}}>{p.unrealized_pl>=0?'+':''}${Number(p.unrealized_pl).toFixed(2)} ({(Number(p.unrealized_plpc)*100).toFixed(1)}%)</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
        {tab==='open orders'&&(
          <div className="table-container"><table>
            <thead><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Type</th><th>Status</th><th>Submitted</th></tr></thead>
            <tbody>
              {(()=>{const open=paperOrders.filter(o=>['new','accepted','pending_new','partially_filled'].includes(o.status));
                if(!open.length) return <tr><td colSpan={6} style={{textAlign:'center',color:'#8b8b9d',padding:24}}>No open orders</td></tr>;
                return open.map((o,i)=>(
                  <tr key={i}><td style={{fontWeight:600}}>{o.symbol}</td>
                    <td><span className={`badge ${o.side==='buy'?'win':'loss'}`}>{o.side.toUpperCase()}</span></td>
                    <td>{o.qty}</td><td>{o.type}</td>
                    <td><span style={{fontSize:'0.7rem',color:'#ecc94b'}}>{o.status}</span></td>
                    <td style={{fontSize:'0.75rem',color:'#8b8b9d'}}>{o.submitted_at?new Date(o.submitted_at).toLocaleTimeString():'-'}</td>
                  </tr>));
              })()}
            </tbody>
          </table></div>
        )}
        {tab==='all orders'&&(
          <div className="table-container"><table>
            <thead><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Type</th><th>Status</th><th>Fill Price</th><th>Submitted</th></tr></thead>
            <tbody>
              {paperOrders.length===0&&<tr><td colSpan={7} style={{textAlign:'center',color:'#8b8b9d',padding:24}}>No orders</td></tr>}
              {paperOrders.map((o,i)=>(
                <tr key={i}><td style={{fontWeight:600}}>{o.symbol}</td>
                  <td><span className={`badge ${o.side==='buy'?'win':'loss'}`}>{o.side.toUpperCase()}</span></td>
                  <td>{o.qty}</td><td>{o.type}</td>
                  <td><span style={{fontSize:'0.7rem',color:o.status==='filled'?'#48bb78':o.status==='canceled'?'#8b8b9d':'#ecc94b'}}>{o.status}</span></td>
                  <td>{o.filled_avg_price?`$${Number(o.filled_avg_price).toFixed(2)}`:'-'}</td>
                  <td style={{fontSize:'0.75rem',color:'#8b8b9d'}}>{o.submitted_at?new Date(o.submitted_at).toLocaleTimeString():'-'}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
        {tab==='scan log'&&(
          <div className="table-container"><table>
            <thead><tr><th>Time</th><th>Signal</th><th>Price</th><th>RSI</th><th>RSI OK</th><th>EMA OK</th></tr></thead>
            <tbody>
              {scanLog.length===0&&<tr><td colSpan={6} style={{textAlign:'center',color:'#8b8b9d',padding:24}}>No scans yet</td></tr>}
              {scanLog.map((log,i)=>(
                <tr key={i}>
                  <td style={{fontSize:'0.75rem',color:'#8b8b9d'}}>{log.time}</td>
                  <td><div style={{width:10,height:10,borderRadius:'50%',background:log.signal?'#48bb78':'#8b8b9d',boxShadow:log.signal?'0 0 6px #48bb78':'none'}}/></td>
                  <td>${log.price||'-'}</td><td>{log.rsi||'-'}</td>
                  <td style={{color:log.rsi_ok?'#48bb78':'#f56565'}}>{log.rsi_ok!==undefined?(log.rsi_ok?'✓':'✗'):'-'}</td>
                  <td style={{color:log.ema_ok?'#48bb78':'#f56565'}}>{log.ema_ok!==undefined?(log.ema_ok?'✓':'✗'):'-'}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
      </div>
    </div>
  );
}

/* ── Live IBKR Panel ─────────────────────────────────────────── */
function LivePanel({ ibkrHost, setIbkrHost, ibkrPort, setIbkrPort, ibkrClientId, setIbkrClientId, ibkrAccount, ibkrConnecting, connStatus, connectIbkr, reconnectIbkr, ibkrPositions, ibkrOrders, msg, setMsg, killSwitch, placeTestOrder, refreshData, ibkrScanning, toggleScanning, ibkrScanLog, ibkrScanSignal, ibkrScanTimingMode, setIbkrScanTimingMode, ibkrScanTimingValue, setIbkrScanTimingValue, spyData, tradePresets, onSavePreset, onLoadPreset, onDeletePreset }) {
  const [tab, setTab] = useState('positions');
  const [cancelLoading, setCancelLoading] = useState({});
  const timingLabel = {interval:'Every N sec',after_open:'N min after open',before_close:'N min before close',on_open:'At market open',on_close:'At market close'};

  const cancelOrder = async (orderId) => {
    setCancelLoading(p=>({...p,[orderId]:true}));
    try {
      const res = await fetch(`${API}/api/ibkr/cancel`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({creds:{host:ibkrHost,port:ibkrPort,client_id:ibkrClientId},orderId})});
      const json = await res.json();
      setMsg(json.msg||json.error||'Cancel submitted');
      await refreshData();
    } catch(e){setMsg(`Error: ${e.message}`);}
    finally{setCancelLoading(p=>({...p,[orderId]:false}));}
  };

  const latestScan = ibkrScanLog[0] || null;

  return (
    <div style={{display:'flex',flexDirection:'column',gap:14}}>
      <AccountHUD account={ibkrAccount} mode="live" connStatus={connStatus} spyData={spyData} onRefresh={refreshData}/>

      <TradingPresetsBar presets={tradePresets} onLoad={onLoadPreset} onSave={onSavePreset} onDelete={onDeletePreset}/>

      {/* Connection */}
      <div style={{background:'var(--bg-card)',borderRadius:12,border:'1px solid var(--border)',padding:20}}>
        <h3 style={{marginBottom:14,fontSize:'0.9rem',color:'#8b8b9d',display:'flex',alignItems:'center',gap:8}}>
          <ShieldCheck size={16}/>Interactive Brokers TWS
        </h3>
        <div style={{display:'grid',gridTemplateColumns:'1fr 100px 80px auto auto',gap:12,alignItems:'flex-end'}}>
          <Field label="TWS Host"><input value={ibkrHost} onChange={e=>setIbkrHost(e.target.value)} style={{...INP,width:'100%'}}/></Field>
          <Field label="Port"><input type="number" value={ibkrPort} onChange={e=>setIbkrPort(Number(e.target.value))} style={{...INP,width:'100%'}}/></Field>
          <Field label="Client ID"><input type="number" value={ibkrClientId} onChange={e=>setIbkrClientId(Number(e.target.value))} min={1} style={{...INP,width:'100%'}}/></Field>
          <button className="btn-primary" onClick={connectIbkr} disabled={ibkrConnecting} style={{width:'auto',padding:'8px 20px',marginTop:0}}>
            <Wifi size={14}/>{ibkrConnecting?'Connecting…':ibkrAccount?'Reconnect':'Connect'}
          </button>
          {connStatus==='dropped'&&(
            <button onClick={reconnectIbkr} style={{padding:'8px 14px',background:'rgba(236,201,75,0.15)',border:'1px solid #ecc94b',borderRadius:8,color:'#ecc94b',cursor:'pointer',fontSize:'0.8rem',fontWeight:700,display:'flex',alignItems:'center',gap:5,marginTop:0}}>
              <RefreshCw size={13}/>Reconnect
            </button>
          )}
        </div>
        <MsgBox msg={msg}/>
      </div>

      {/* Scanner */}
      <div style={{background:'var(--bg-card)',borderRadius:12,border:'1px solid var(--border)',padding:20}}>
        <h3 style={{marginBottom:14,fontSize:'0.9rem',color:'#8b8b9d',display:'flex',alignItems:'center',gap:8}}><Activity size={16}/>Scanner & Controls</h3>
        <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:12,marginBottom:12}}>
          <Field label="Scan Timing">
            <select value={ibkrScanTimingMode} onChange={e=>setIbkrScanTimingMode(e.target.value)} style={SEL}>
              {Object.entries(timingLabel).map(([k,v])=><option key={k} value={k}>{v}</option>)}
            </select>
          </Field>
          {['interval','after_open','before_close'].includes(ibkrScanTimingMode)&&(
            <Field label={ibkrScanTimingMode==='interval'?'Interval (sec)':'Offset (min)'}>
              <input type="number" value={ibkrScanTimingValue} onChange={e=>setIbkrScanTimingValue(Number(e.target.value))} min={1} style={{...INP,width:'100%'}}/>
            </Field>
          )}
        </div>
        <div style={{display:'flex',gap:10,flexWrap:'wrap',alignItems:'center'}}>
          <button onClick={ibkrScanSignal} className="btn-secondary" style={{padding:'8px 14px',display:'flex',alignItems:'center',gap:6}}><RefreshCw size={14}/>Scan Now</button>
          <button onClick={toggleScanning} style={{padding:'8px 18px',borderRadius:8,border:`1px solid ${ibkrScanning?'#f56565':'var(--accent)'}`,background:ibkrScanning?'rgba(245,101,101,0.15)':'rgba(107,70,193,0.2)',color:ibkrScanning?'#f56565':'#a78bfa',cursor:'pointer',fontWeight:700,fontSize:'0.8rem',display:'flex',alignItems:'center',gap:6}}>
            <Radio size={14}/>{ibkrScanning?'Stop Scan':'Start Scan'}
          </button>
          <button onClick={placeTestOrder} className="btn-secondary" style={{padding:'8px 14px',display:'flex',alignItems:'center',gap:6}}><Zap size={14}/>Test Order</button>
          <button onClick={killSwitch} style={{padding:'8px 14px',background:'rgba(245,101,101,0.15)',border:'1px solid #f56565',borderRadius:8,color:'#f56565',cursor:'pointer',display:'flex',alignItems:'center',gap:6,fontSize:'0.8rem',fontWeight:700}}>
            <XCircle size={14}/>KILL SWITCH
          </button>
        </div>
        {ibkrScanning&&<div style={{marginTop:8,fontSize:'0.72rem',color:'#8b8b9d'}}>Scanning · {timingLabel[ibkrScanTimingMode]}{['interval','after_open','before_close'].includes(ibkrScanTimingMode)?` (${ibkrScanTimingValue})`:''}</div>}
        {latestScan&&<div style={{marginTop:12}}><SignalBadge signal={latestScan}/></div>}
      </div>

      {/* Data */}
      <div>
        <div style={{display:'flex',gap:4,borderBottom:'1px solid var(--border)'}}>
          {['positions','open orders','scan log'].map(t=>(
            <button key={t} onClick={()=>setTab(t)} style={{padding:'8px 16px',background:'none',border:'none',borderBottom:tab===t?'2px solid var(--accent)':'2px solid transparent',color:tab===t?'#a78bfa':'#8b8b9d',fontWeight:600,fontSize:'0.8rem',cursor:'pointer',textTransform:'capitalize'}}>{t}</button>
          ))}
          <button onClick={refreshData} style={{marginLeft:'auto',background:'none',border:'none',color:'#8b8b9d',cursor:'pointer',padding:'8px 12px',display:'flex',alignItems:'center'}}><RefreshCw size={14}/></button>
        </div>
        {tab==='positions'&&(
          <div className="table-container"><table>
            <thead><tr><th>Symbol</th><th>Type</th><th>Qty</th><th>Avg Price</th><th>Market Price</th><th>Unrealized P&L</th><th>Realized P&L</th></tr></thead>
            <tbody>
              {ibkrPositions.length===0&&<tr><td colSpan={7} style={{textAlign:'center',color:'#8b8b9d',padding:24}}>No open positions — connect to TWS first</td></tr>}
              {ibkrPositions.map((p,i)=>(
                <tr key={i}>
                  <td style={{fontWeight:600}}>{p.symbol}</td>
                  <td><span style={{fontSize:'0.7rem',color:'#8b8b9d'}}>{p.type}</span></td>
                  <td>{p.qty}</td><td>${Number(p.avg_price).toFixed(2)}</td><td>${Number(p.market_price).toFixed(2)}</td>
                  <td style={{color:p.unrealized_pl>=0?'#48bb78':'#f56565',fontWeight:600}}>{p.unrealized_pl>=0?'+':''}${Number(p.unrealized_pl).toFixed(2)}</td>
                  <td style={{color:p.realized_pl>=0?'#48bb78':'#f56565'}}>${Number(p.realized_pl).toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
        {tab==='open orders'&&(
          <div className="table-container"><table>
            <thead><tr><th>Order ID</th><th>Symbol</th><th>Action</th><th>Qty</th><th>Type</th><th>Limit</th><th>Status</th><th>Cancel</th></tr></thead>
            <tbody>
              {ibkrOrders.length===0&&<tr><td colSpan={8} style={{textAlign:'center',color:'#8b8b9d',padding:24}}>No open orders</td></tr>}
              {ibkrOrders.map((o,i)=>(
                <tr key={i}>
                  <td style={{fontSize:'0.75rem',color:'#8b8b9d'}}>{o.orderId}</td>
                  <td style={{fontWeight:600}}>{o.symbol}</td>
                  <td><span className={`badge ${o.action==='BUY'?'win':'loss'}`}>{o.action}</span></td>
                  <td>{o.qty}</td><td>{o.type}</td>
                  <td>{o.lmtPrice?`$${Number(o.lmtPrice).toFixed(2)}`:'MKT'}</td>
                  <td><span style={{fontSize:'0.7rem',color:'#ecc94b'}}>{o.status}</span></td>
                  <td><button onClick={()=>cancelOrder(o.orderId)} disabled={cancelLoading[o.orderId]} style={{padding:'4px 10px',background:'rgba(245,101,101,0.1)',border:'1px solid #f56565',borderRadius:6,color:'#f56565',cursor:'pointer',fontSize:'0.72rem',fontWeight:600}}>{cancelLoading[o.orderId]?'…':'Cancel'}</button></td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
        {tab==='scan log'&&(
          <div className="table-container"><table>
            <thead><tr><th>Time</th><th>Signal</th><th>Price</th><th>RSI</th><th>RSI OK</th><th>EMA OK</th><th>Strategy</th></tr></thead>
            <tbody>
              {ibkrScanLog.length===0&&<tr><td colSpan={7} style={{textAlign:'center',color:'#8b8b9d',padding:24}}>No scans yet</td></tr>}
              {ibkrScanLog.map((log,i)=>(
                <tr key={i}>
                  <td style={{fontSize:'0.75rem',color:'#8b8b9d'}}>{log.time}</td>
                  <td><div style={{width:10,height:10,borderRadius:'50%',background:log.signal?'#48bb78':'#8b8b9d',boxShadow:log.signal?'0 0 6px #48bb78':'none'}}/></td>
                  <td>${log.price||'-'}</td><td>{log.rsi||'-'}</td>
                  <td style={{color:log.rsi_ok?'#48bb78':'#f56565'}}>{log.rsi_ok!==undefined?(log.rsi_ok?'✓':'✗'):'-'}</td>
                  <td style={{color:log.ema_ok?'#48bb78':'#f56565'}}>{log.ema_ok!==undefined?(log.ema_ok?'✓':'✗'):'-'}</td>
                  <td style={{fontSize:'0.75rem',color:'#8b8b9d'}}>{log.strategy||'-'}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
      </div>
    </div>
  );
}

/* ── Main App ─────────────────────────────────────────────────── */
export default function App() {
  const [appMode, setAppMode] = useState('backtest');
  const [config, setConfig] = useState(loadConfig);
  const [strategies, setStrategies] = useState([]);
  const [customPresets, setCustomPresets] = useState(loadPresets);
  const [tradePresets, setTradePresets] = useState(loadTradePresets);
  const [spyData, setSpyData] = useState(null);
  const [presetName, setPresetName] = useState('');
  const [currentTime, setCurrentTime] = useState(new Date());

  // Backtest
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [apiError, setApiError] = useState(null);
  const [activeTab, setActiveTab] = useState('chart');
  const [optimizing, setOptimizing] = useState(false);
  const [optimizerResult, setOptimizerResult] = useState(null);
  const [optParamX, setOptParamX] = useState('entry_red_days');
  const [optParamY, setOptParamY] = useState('target_dte');

  // Paper
  const [paperKey, setPaperKey] = useState(localStorage.getItem('alpaca_key')||'');
  const [paperSecret, setPaperSecret] = useState(localStorage.getItem('alpaca_secret')||'');
  const [paperAccount, setPaperAccount] = useState(null);
  const [paperConnecting, setPaperConnecting] = useState(false);
  const [paperPositions, setPaperPositions] = useState([]);
  const [paperOrders, setPaperOrders] = useState([]);
  const [paperSignal, setPaperSignal] = useState(null);
  const [paperMsg, setPaperMsg] = useState('');
  const [paperScanning, setPaperScanning] = useState(false);
  const [paperAutoExec, setPaperAutoExec] = useState(false);
  const [scanTimingMode, setScanTimingMode] = useState('interval');
  const [scanTimingValue, setScanTimingValue] = useState(300);
  const [scanLog, setScanLog] = useState([]);
  const autoScanRef = useRef(null);
  const paperKeyRef = useRef(paperKey);
  const paperSecretRef = useRef(paperSecret);

  // IBKR
  const [ibkrHost, setIbkrHost] = useState('127.0.0.1');
  const [ibkrPort, setIbkrPort] = useState(7497);
  const [ibkrClientId, setIbkrClientId] = useState(1);
  const [ibkrAccount, setIbkrAccount] = useState(null);
  const [ibkrConnecting, setIbkrConnecting] = useState(false);
  const [ibkrPositions, setIbkrPositions] = useState([]);
  const [ibkrOrders, setIbkrOrders] = useState([]);
  const [connStatus, setConnStatus] = useState('offline');
  const [lastHeartbeat, setLastHeartbeat] = useState(null);
  const [ibkrMsg, setIbkrMsg] = useState('');
  const [ibkrScanning, setIbkrScanning] = useState(false);
  const [ibkrScanLog, setIbkrScanLog] = useState([]);
  const [ibkrScanTimingMode, setIbkrScanTimingMode] = useState('interval');
  const [ibkrScanTimingValue, setIbkrScanTimingValue] = useState(60);
  const ibkrAutoScanRef = useRef(null);

  // Chart
  const chartContainerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const markersPluginRef = useRef(null);
  const configRef = useRef(config);
  useEffect(()=>{configRef.current=config;},[config]);
  useEffect(()=>{paperKeyRef.current=paperKey;},[paperKey]);
  useEffect(()=>{paperSecretRef.current=paperSecret;},[paperSecret]);

  const mkt = useCallback(()=>{
    const now=new Date(),day=now.getDay(),h=now.getUTCHours(),mn=now.getUTCMinutes();
    if(day===0||day===6) return {label:'CLOSED',color:'#f56565'};
    const t=h+mn/60;
    return(t>=13.5&&t<20)?{label:'OPEN',color:'#48bb78'}:{label:'CLOSED',color:'#f56565'};
  },[])();

  const handleChange = useCallback((e)=>{
    const{name,value,type,checked}=e.target;
    setConfig(prev=>{
      const next={...prev,[name]:type==='checkbox'?checked:isNaN(Number(value))?value:value===''?value:Number(value)};
      localStorage.setItem(STORAGE_KEY,JSON.stringify(next));
      return next;
    });
  },[]);

  // Chart init
  useEffect(()=>{
    if(!chartContainerRef.current) return;
    const chart=LWCharts.createChart(chartContainerRef.current,{
      autoSize:true,
      layout:{background:{color:'transparent'},textColor:'#8b8b9d'},
      grid:{vertLines:{color:'rgba(255,255,255,0.04)'},horzLines:{color:'rgba(255,255,255,0.04)'}},
      crosshair:{mode:1},
      rightPriceScale:{borderColor:'rgba(255,255,255,0.1)'},
      timeScale:{borderColor:'rgba(255,255,255,0.1)',timeVisible:true},
    });
    chartRef.current=chart;
    const series=chart.addSeries(LWCharts.CandlestickSeries,{upColor:'#48bb78',downColor:'#f56565',borderVisible:false,wickUpColor:'#48bb78',wickDownColor:'#f56565'});
    seriesRef.current=series;
    markersPluginRef.current=LWCharts.createSeriesMarkers(series,[]);
    return()=>{chartRef.current?.remove();chartRef.current=null;seriesRef.current=null;markersPluginRef.current=null;};
  },[]);

  // Chart data update
  useEffect(()=>{
    if(!result||!seriesRef.current) return;
    const prices=(result.price_history||[]).filter(p=>p.open!=null&&p.high!=null);
    try{seriesRef.current.setData(prices);}catch{return;}
    const markers=[];
    (result.trades||[]).forEach(t=>{
      if(t.entry_date) markers.push({time:t.entry_date,position:'belowBar',color:'#48bb78',shape:'arrowUp',text:config.direction==='bear'?'PUT':'BUY'});
      if(t.exit_date) markers.push({time:t.exit_date,position:'aboveBar',color:t.win?'#48bb78':'#f56565',shape:'arrowDown',text:t.stopped_out?'STOP':t.reason==='expired'?'EXP':t.win?'WIN':'LOSS'});
    });
    markers.sort((a,b)=>a.time<b.time?-1:1);
    markersPluginRef.current?.setMarkers(markers);
    chartRef.current?.timeScale().fitContent();
  },[result,config.direction]);

  useEffect(()=>{const t=setInterval(()=>setCurrentTime(new Date()),1000);return()=>clearInterval(t);},[]);

  const runSimulation = useCallback(async()=>{
    setLoading(true);setApiError(null);
    try{
      const res=await fetch(`${API}/api/backtest`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(config)});
      const json=await res.json();
      if(json.error) throw new Error(json.error);
      setResult(json);
    }catch(e){setApiError(e.message);}finally{setLoading(false);}
  },[config]);

  useEffect(()=>{
    fetch(`${API}/api/strategies`).then(r=>r.json()).then(setStrategies).catch(()=>{});
    runSimulation();
  },[]); // eslint-disable-line

  // Paper
  const loadPaperData = useCallback(async()=>{
    if(!paperKeyRef.current||!paperSecretRef.current) return;
    try{
      const creds={api_key:paperKeyRef.current,api_secret:paperSecretRef.current};
      const[posRes,ordRes]=await Promise.all([
        fetch(`${API}/api/paper/positions`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(creds)}),
        fetch(`${API}/api/paper/orders`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(creds)}),
      ]);
      setPaperPositions((await posRes.json()).positions||[]);
      setPaperOrders((await ordRes.json()).orders||[]);
    }catch{}
  },[]);

  const connectPaper = async()=>{
    setPaperConnecting(true);setPaperMsg('');
    localStorage.setItem('alpaca_key',paperKey);
    localStorage.setItem('alpaca_secret',paperSecret);
    try{
      const res=await fetch(`${API}/api/paper/connect`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key:paperKey,api_secret:paperSecret})});
      const json=await res.json();
      if(json.connected){setPaperAccount(json);loadPaperData();setPaperMsg('Connected to Alpaca Paper!');}
      else setPaperMsg(`Error: ${json.error||'Connection failed'}`);
    }catch(e){setPaperMsg(`Error: ${e.message}`);}finally{setPaperConnecting(false);}
  };

  const scanSignal = useCallback(async(isAuto=false)=>{
    try{
      const res=await fetch(`${API}/api/paper/scan`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key:paperKeyRef.current,api_secret:paperSecretRef.current,config:configRef.current})});
      const data=await res.json();
      setPaperSignal(data);
      setScanLog(prev=>[{time:new Date().toLocaleTimeString(),...data},...prev].slice(0,30));
      if(isAuto&&data.signal&&paperAutoExec&&paperKeyRef.current&&paperSecretRef.current){
        const side=configRef.current.direction==='bear'?'sell':'buy';
        await fetch(`${API}/api/paper/execute`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key:paperKeyRef.current,api_secret:paperSecretRef.current,symbol:configRef.current.ticker,qty:configRef.current.contracts_per_trade*100,side})});
        loadPaperData();
      }
    }catch{}
  },[paperAutoExec,loadPaperData]);

  const togglePaperScanning = useCallback(()=>{
    if(paperScanning){
      clearInterval(autoScanRef.current);
      setPaperScanning(false);
    } else {
      const secs = scanTimingMode==='interval' ? Math.max(scanTimingValue,10) : 300;
      scanSignal(true);
      autoScanRef.current = setInterval(()=>scanSignal(true), secs*1000);
      setPaperScanning(true);
    }
  },[paperScanning,scanTimingMode,scanTimingValue,scanSignal]);

  const paperKillSwitch = useCallback(async()=>{
    clearInterval(autoScanRef.current);setPaperScanning(false);
    setPaperMsg('Kill switch — stopping scanner, closing positions…');
    for(const pos of paperPositions){
      try{await fetch(`${API}/api/paper/execute`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key:paperKeyRef.current,api_secret:paperSecretRef.current,symbol:pos.symbol,qty:Math.abs(pos.qty),side:pos.side==='long'?'sell':'buy'})});}catch{}
    }
    await loadPaperData();
    setPaperMsg('Kill switch activated — all positions closed.');
  },[paperPositions,loadPaperData]);

  useEffect(()=>{
    if(appMode!=='paper'||!paperAccount) return;
    const t=setInterval(loadPaperData,30000);return()=>clearInterval(t);
  },[appMode,paperAccount,loadPaperData]);

  // IBKR
  const loadIbkrData = useCallback(async()=>{
    try{
      const[posRes,ordRes]=await Promise.all([
        fetch(`${API}/api/ibkr/positions`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({host:ibkrHost,port:ibkrPort,client_id:ibkrClientId})}),
        fetch(`${API}/api/ibkr/orders?host=${ibkrHost}&port=${ibkrPort}&client_id=${ibkrClientId}`),
      ]);
      setIbkrPositions((await posRes.json()).positions||[]);
      setIbkrOrders((await ordRes.json()).orders||[]);
    }catch{}
  },[ibkrHost,ibkrPort,ibkrClientId]);

  const connectIbkr = async()=>{
    setIbkrConnecting(true);setIbkrMsg('');
    try{
      const res=await fetch(`${API}/api/ibkr/connect`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({host:ibkrHost,port:ibkrPort,client_id:ibkrClientId})});
      const json=await res.json();
      if(json.connected){setIbkrAccount(json.summary);setConnStatus('online');setLastHeartbeat(new Date());loadIbkrData();setIbkrMsg('Connected to TWS!');}
      else{setConnStatus('offline');setIbkrMsg(`Error: ${json.error||'Connection failed'}`);}
    }catch(e){setConnStatus('offline');setIbkrMsg(`Error: ${e.message}`);}finally{setIbkrConnecting(false);}
  };

  const ibkrKillSwitch = useCallback(async()=>{
    clearInterval(ibkrAutoScanRef.current);setIbkrScanning(false);
    setIbkrMsg('Kill switch — cancelling all orders…');
    for(const ord of ibkrOrders){
      try{await fetch(`${API}/api/ibkr/cancel`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({creds:{host:ibkrHost,port:ibkrPort,client_id:ibkrClientId},orderId:ord.orderId})});}catch{}
    }
    await loadIbkrData();
    setIbkrMsg('Kill switch activated — all orders cancelled, scanner stopped.');
  },[ibkrOrders,ibkrHost,ibkrPort,ibkrClientId,loadIbkrData]);

  const placeIbkrTestOrder = useCallback(async()=>{
    try{
      const res=await fetch(`${API}/api/ibkr/test_order`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({host:ibkrHost,port:ibkrPort,client_id:ibkrClientId})});
      const json=await res.json();
      setIbkrMsg(json.msg||json.error||JSON.stringify(json));
      await loadIbkrData();
    }catch(e){setIbkrMsg(`Error: ${e.message}`);}
  },[ibkrHost,ibkrPort,ibkrClientId,loadIbkrData]);

  const ibkrScanSignal = useCallback(async()=>{
    try{
      const res=await fetch(`${API}/api/paper/scan`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key:'',api_secret:'',config:configRef.current})});
      const data=await res.json();
      setIbkrScanLog(prev=>[{time:new Date().toLocaleTimeString(),...data},...prev].slice(0,30));
    }catch{}
  },[]);

  const toggleIbkrScanning = useCallback(()=>{
    if(ibkrScanning){
      clearInterval(ibkrAutoScanRef.current);
      setIbkrScanning(false);
    } else {
      const secs = ibkrScanTimingMode==='interval' ? Math.max(ibkrScanTimingValue,10) : 60;
      ibkrScanSignal();
      ibkrAutoScanRef.current = setInterval(ibkrScanSignal, secs*1000);
      setIbkrScanning(true);
    }
  },[ibkrScanning,ibkrScanTimingMode,ibkrScanTimingValue,ibkrScanSignal]);

  // Reconnect handler (force-reconnect via /api/ibkr/reconnect)
  const reconnectIbkr = useCallback(async()=>{
    setIbkrConnecting(true);setIbkrMsg('Reconnecting…');
    try{
      const res=await fetch(`${API}/api/ibkr/reconnect`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({host:ibkrHost,port:ibkrPort,client_id:ibkrClientId})});
      const json=await res.json();
      if(json.connected){setIbkrAccount(json.summary);setConnStatus('online');setLastHeartbeat(new Date());loadIbkrData();setIbkrMsg('Reconnected!');}
      else{setConnStatus('offline');setIbkrMsg(`Error: ${json.error||'Reconnect failed'}`);}
    }catch(e){setConnStatus('offline');setIbkrMsg(`Error: ${e.message}`);}finally{setIbkrConnecting(false);}
  },[ibkrHost,ibkrPort,ibkrClientId,loadIbkrData]);

  // IBKR heartbeat — lightweight /heartbeat endpoint, auto-reconnect on drop
  useEffect(()=>{
    if(appMode!=='live') return;
    const hb=async()=>{
      try{
        const res=await fetch(`${API}/api/ibkr/heartbeat`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({host:ibkrHost,port:ibkrPort,client_id:ibkrClientId})});
        const json=await res.json();
        if(json.alive){setConnStatus('online');setLastHeartbeat(new Date());}
        else if(connStatus==='online'){setConnStatus('dropped');}
      }catch{if(connStatus==='online') setConnStatus('dropped');}
    };
    const t=setInterval(hb,10000);return()=>clearInterval(t);
  },[appMode,ibkrHost,ibkrPort,ibkrClientId,connStatus]);

  useEffect(()=>{
    if(appMode!=='live'||!ibkrAccount) return;
    const t=setInterval(loadIbkrData,30000);return()=>clearInterval(t);
  },[appMode,ibkrAccount,loadIbkrData]);

  // SPY intraday sparkline polling
  useEffect(()=>{
    if(appMode!=='paper'&&appMode!=='live') return;
    const fetchSpy=()=>fetch(`${API}/api/spy/intraday`).then(r=>r.json()).then(d=>{ if(!d.error) setSpyData(d); }).catch(()=>{});
    fetchSpy();
    const t=setInterval(fetchSpy,60000);
    return()=>clearInterval(t);
  },[appMode]);

  // Trading presets
  const saveTradePreset = useCallback((name)=>{
    const preset={ config, scanner:{ timing_mode:scanTimingMode, timing_value:scanTimingValue, auto_execute:paperAutoExec }, ibkr_scanner:{ timing_mode:ibkrScanTimingMode, timing_value:ibkrScanTimingValue } };
    const updated={...tradePresets,[name]:preset};
    setTradePresets(updated);
    localStorage.setItem(TRADE_PRESETS_KEY,JSON.stringify(updated));
  },[config,scanTimingMode,scanTimingValue,paperAutoExec,ibkrScanTimingMode,ibkrScanTimingValue,tradePresets]);

  const loadTradePreset = useCallback((preset)=>{
    if(preset.config){ const next={...DEFAULT_CONFIG,...preset.config}; setConfig(next); localStorage.setItem(STORAGE_KEY,JSON.stringify(next)); }
    if(preset.scanner){ setScanTimingMode(preset.scanner.timing_mode||'interval'); setScanTimingValue(preset.scanner.timing_value||300); if(preset.scanner.auto_execute!==undefined) setPaperAutoExec(preset.scanner.auto_execute); }
    if(preset.ibkr_scanner){ setIbkrScanTimingMode(preset.ibkr_scanner.timing_mode||'interval'); setIbkrScanTimingValue(preset.ibkr_scanner.timing_value||60); }
  },[]);

  const deleteTradePreset = useCallback((name)=>{
    const{[name]:_,...rest}=tradePresets;
    setTradePresets(rest);
    localStorage.setItem(TRADE_PRESETS_KEY,JSON.stringify(rest));
  },[tradePresets]);

  // Presets
  const allPresets={...BUILT_IN_PRESETS,...customPresets};
  const applyPreset=(name)=>{
    const p=allPresets[name];if(!p) return;
    const next={...DEFAULT_CONFIG,...p};setConfig(next);localStorage.setItem(STORAGE_KEY,JSON.stringify(next));
  };
  const savePreset=()=>{
    if(!presetName.trim()) return;
    const updated={...customPresets,[presetName.trim()]:{...config}};
    setCustomPresets(updated);localStorage.setItem(PRESETS_KEY,JSON.stringify(updated));setPresetName('');
  };
  const deletePreset=(name)=>{
    const{[name]:_,...rest}=customPresets;
    setCustomPresets(rest);localStorage.setItem(PRESETS_KEY,JSON.stringify(rest));
  };

  const runOptimizer=async()=>{
    setOptimizing(true);
    const ranges={entry_red_days:[1,2,3,4],target_dte:[7,14,21,30],stop_loss_pct:[25,50,75,100],rsi_threshold:[20,25,30,35]};
    try{
      const res=await fetch(`${API}/api/optimize`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({base_config:config,param_x:optParamX,param_y:optParamY,x_values:ranges[optParamX]||[1,2,3,4],y_values:ranges[optParamY]||[7,14,21,30]})});
      setOptimizerResult(await res.json());
    }catch{}finally{setOptimizing(false);}
  };

  const m=result?.metrics??{};
  const S=(s)=>({width:'100%',background:'var(--bg-card)',border:'1px solid var(--border)',color:'#fff',padding:'8px 12px',borderRadius:8,fontFamily:'inherit',fontSize:'0.85rem',...(s||{})});

  return (
    <div className="app-container">
      {/* SIDEBAR */}
      <div className="sidebar">
        <div className="sidebar-header">
          <h1><Activity size={22}/>Neural Engine</h1>
          <p style={{fontSize:'0.62rem',color:'#8b8b9d',marginTop:-4,fontWeight:700,textTransform:'uppercase'}}>SPY Options Backtester v2.0</p>
        </div>

        <Section label="Engine Mode">
          <div style={{display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:6,marginBottom:4}}>
            {['backtest','paper','live'].map(mode=>(
              <button key={mode} onClick={()=>setAppMode(mode)} style={{padding:'10px 5px',borderRadius:8,border:`1px solid ${appMode===mode?'var(--accent)':'var(--border)'}`,background:appMode===mode?'rgba(107,70,193,0.2)':'var(--bg-card)',color:appMode===mode?'#a78bfa':'#8b8b9d',fontSize:'0.7rem',fontWeight:700,cursor:'pointer',textTransform:'uppercase'}}>{mode}</button>
            ))}
          </div>
        </Section>

        <Section label="Strategy">
          <Field label="Logic Engine">
            <select name="strategy_id" value={config.strategy_id} onChange={handleChange} style={S()}>
              {strategies.length>0?strategies.map(s=><option key={s.id} value={s.id}>{s.name}</option>):<><option value="consecutive_days">Consecutive Days</option><option value="combo_spread">Combo Spread</option></>}
            </select>
          </Field>
          <Field label="Topology">
            <select name="topology" value={config.topology} onChange={handleChange} style={S()}>
              <option value="vertical_spread">Vertical Spread</option>
              <option value="long_call">Long Call</option>
              <option value="long_put">Long Put</option>
              <option value="straddle">Straddle</option>
              <option value="iron_condor">Iron Condor</option>
              <option value="butterfly">Butterfly</option>
            </select>
          </Field>
          <Field label="Direction">
            <select name="direction" value={config.direction} onChange={handleChange} style={S()}>
              <option value="bull">Bull</option><option value="bear">Bear</option><option value="neutral">Neutral</option>
            </select>
          </Field>
          <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:8}}>
            <Field label="Strike Width"><Inp name="strike_width" value={config.strike_width} onChange={handleChange} min={1}/></Field>
            <Field label="Target DTE"><Inp name="target_dte" value={config.target_dte} onChange={handleChange} min={1}/></Field>
          </div>
        </Section>

        <Section label="Entry Rules">
          {config.strategy_id==='consecutive_days'&&(
            <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:8}}>
              <Field label="Red Days In"><Inp name="entry_red_days" value={config.entry_red_days} onChange={handleChange} min={1}/></Field>
              <Field label="Green Days Out"><Inp name="exit_green_days" value={config.exit_green_days} onChange={handleChange} min={1}/></Field>
            </div>
          )}
          {config.strategy_id==='combo_spread'&&(
            <>
              <div style={{display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:6}}>
                <Field label="SMA1"><Inp name="combo_sma1" value={config.combo_sma1} onChange={handleChange}/></Field>
                <Field label="SMA2"><Inp name="combo_sma2" value={config.combo_sma2} onChange={handleChange}/></Field>
                <Field label="SMA3"><Inp name="combo_sma3" value={config.combo_sma3} onChange={handleChange}/></Field>
              </div>
              <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:6}}>
                <Field label="Max Bars"><Inp name="combo_max_bars" value={config.combo_max_bars} onChange={handleChange}/></Field>
                <Field label="Profit Closes"><Inp name="combo_max_profit_closes" value={config.combo_max_profit_closes} onChange={handleChange}/></Field>
              </div>
            </>
          )}
        </Section>

        <Section label="Filters">
          <Toggle name="use_rsi_filter" label="RSI Filter" checked={config.use_rsi_filter} onChange={handleChange}/>
          {config.use_rsi_filter&&<Field label="RSI Threshold"><Inp name="rsi_threshold" value={config.rsi_threshold} onChange={handleChange} min={1} max={99}/></Field>}
          <Toggle name="use_ema_filter" label="EMA Filter" checked={config.use_ema_filter} onChange={handleChange}/>
          {config.use_ema_filter&&<Field label="EMA Length"><Inp name="ema_length" value={config.ema_length} onChange={handleChange} min={1}/></Field>}
          <Toggle name="use_sma200_filter" label="SMA200 Filter" checked={config.use_sma200_filter} onChange={handleChange}/>
          <Toggle name="use_volume_filter" label="Volume Filter" checked={config.use_volume_filter} onChange={handleChange}/>
          <Toggle name="use_vix_filter" label="VIX Filter" checked={config.use_vix_filter} onChange={handleChange}/>
          {config.use_vix_filter&&(
            <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:8}}>
              <Field label="VIX Min"><Inp name="vix_min" value={config.vix_min} onChange={handleChange}/></Field>
              <Field label="VIX Max"><Inp name="vix_max" value={config.vix_max} onChange={handleChange}/></Field>
            </div>
          )}
          <Toggle name="use_regime_filter" label="Regime Filter" checked={config.use_regime_filter} onChange={handleChange}/>
          {config.use_regime_filter&&(
            <Field label="Allowed Regime">
              <select name="regime_allowed" value={config.regime_allowed} onChange={handleChange} style={S()}>
                <option value="all">All</option><option value="bull">Bull</option><option value="bear">Bear</option><option value="sideways">Sideways</option>
              </select>
            </Field>
          )}
        </Section>

        <Section label="Risk & Position Sizing">
          <Field label="Capital ($)"><Inp name="capital_allocation" value={config.capital_allocation} onChange={handleChange} min={100}/></Field>
          <Toggle name="use_dynamic_sizing" label="Dynamic Sizing" checked={config.use_dynamic_sizing} onChange={handleChange}/>
          {config.use_dynamic_sizing?(
            <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:8}}>
              <Field label="Risk % / Trade"><Inp name="risk_percent" value={config.risk_percent} onChange={handleChange} step={0.5} min={0.5}/></Field>
              <Field label="Max Cap ($)"><Inp name="max_trade_cap" value={config.max_trade_cap} onChange={handleChange}/></Field>
            </div>
          ):(
            <Field label="Contracts / Trade"><Inp name="contracts_per_trade" value={config.contracts_per_trade} onChange={handleChange} min={1}/></Field>
          )}
          <Toggle name="use_targeted_spread" label="Target Spread %" checked={config.use_targeted_spread} onChange={handleChange}/>
          {config.use_targeted_spread&&(
            <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:8}}>
              <Field label="% of Capital"><Inp name="target_spread_pct" value={config.target_spread_pct} onChange={handleChange} step={0.5}/></Field>
              <Field label="Max Alloc Cap ($)"><Inp name="max_allocation_cap" value={config.max_allocation_cap} onChange={handleChange}/></Field>
            </div>
          )}
          <Field label="Spread Cost Target ($)"><Inp name="spread_cost_target" value={config.spread_cost_target} onChange={handleChange}/></Field>
          <Field label="Commission / Contract"><Inp name="commission_per_contract" value={config.commission_per_contract} onChange={handleChange} step={0.01}/></Field>
        </Section>

        <Section label="Exit Rules">
          <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:8}}>
            <Field label="Stop Loss %"><Inp name="stop_loss_pct" value={config.stop_loss_pct} onChange={handleChange} min={0}/></Field>
            <Field label="Take Profit %"><Inp name="take_profit_pct" value={config.take_profit_pct} onChange={handleChange} min={0}/></Field>
            <Field label="Trailing Stop %"><Inp name="trailing_stop_pct" value={config.trailing_stop_pct} onChange={handleChange} min={0}/></Field>
          </div>
        </Section>

        <Section label="Advanced">
          <Toggle name="use_mark_to_market" label="Mark-to-Market" checked={config.use_mark_to_market} onChange={handleChange}/>
          <Toggle name="enable_mc_histogram" label="Monte Carlo" checked={config.enable_mc_histogram} onChange={handleChange}/>
          <Toggle name="enable_walk_forward" label="Walk Forward" checked={config.enable_walk_forward} onChange={handleChange}/>
          {config.enable_walk_forward&&<Field label="WF Windows"><Inp name="walk_forward_windows" value={config.walk_forward_windows} onChange={handleChange} min={2} max={10}/></Field>}
          <Field label="IV Realism Factor"><Inp name="realism_factor" value={config.realism_factor} onChange={handleChange} step={0.05} min={1}/></Field>
          <Field label="Years History"><Inp name="years_history" value={config.years_history} onChange={handleChange} min={1} max={10}/></Field>
        </Section>

        <Section label="Presets">
          <div style={{display:'flex',flexWrap:'wrap',gap:6,marginBottom:10}}>
            {Object.keys(allPresets).map(name=>(
              <div key={name} style={{display:'flex',gap:2,alignItems:'center'}}>
                <button className="preset-chip" onClick={()=>applyPreset(name)}>{name}</button>
                {customPresets[name]&&<button onClick={()=>deletePreset(name)} style={{background:'none',border:'none',color:'#f56565',cursor:'pointer',fontSize:'0.7rem',padding:'2px 4px'}}>✕</button>}
              </div>
            ))}
          </div>
          <div style={{display:'flex',gap:6}}>
            <input placeholder="Preset name…" value={presetName} onChange={e=>setPresetName(e.target.value)} onKeyDown={e=>e.key==='Enter'&&savePreset()} style={{flex:1,background:'var(--bg-card)',border:'1px solid var(--border)',borderRadius:8,padding:'7px 10px',color:'#fff',fontSize:'0.8rem',fontFamily:'inherit'}}/>
            <button onClick={savePreset} className="btn-secondary" style={{padding:'7px 14px'}}><Save size={14}/></button>
          </div>
        </Section>

        {appMode==='backtest'&&(
          <button className="btn-primary" onClick={runSimulation} disabled={loading} style={{marginTop:8}}>
            <Play size={16}/>{loading?'Running…':'Run Simulation'}
          </button>
        )}
      </div>

      {/* MAIN */}
      <div className="main-content">
        <header className="dashboard-header">
          <div style={{display:'flex',gap:24,alignItems:'center'}}>
            <div className="hud-item"><span className="hud-label">System Time</span><span className="hud-value">{currentTime.toLocaleTimeString([],{hour12:false})}</span></div>
            <div className="hud-item"><span className="hud-label">Market</span><span className="hud-value" style={{color:mkt.color}}>{mkt.label}</span></div>
            {appMode==='live'&&<div className="hud-item"><span className="hud-label">TWS</span><span className="hud-value" style={{color:connStatus==='online'?'#48bb78':connStatus==='dropped'?'#ecc94b':'#f56565'}}>{connStatus.toUpperCase()}</span></div>}
            {appMode==='live'&&ibkrAccount&&<>
              <div className="hud-item"><span className="hud-label">Net Liq</span><span className="hud-value">${Number(ibkrAccount.equity||0).toLocaleString()}</span></div>
              <div className="hud-item"><span className="hud-label">Day P&L</span><span className="hud-value" style={{color:Number(ibkrAccount.daily_pnl||0)>=0?'#48bb78':'#f56565'}}>${Number(ibkrAccount.daily_pnl||0).toFixed(0)}</span></div>
              <div className="hud-item"><span className="hud-label">BP</span><span className="hud-value">${Number(ibkrAccount.buying_power||0).toLocaleString()}</span></div>
            </>}
            {appMode==='paper'&&paperAccount&&<>
              <div className="hud-item"><span className="hud-label">Equity</span><span className="hud-value">${Number(paperAccount.equity||0).toLocaleString()}</span></div>
              <div className="hud-item"><span className="hud-label">BP</span><span className="hud-value">${Number(paperAccount.buying_power||0).toLocaleString()}</span></div>
            </>}
            {appMode==='paper'&&paperScanning&&<div className="hud-item"><span className="hud-label">Scanner</span><span className="hud-value live-glow" style={{color:'#48bb78'}}>● LIVE</span></div>}
          </div>
          <div style={{display:'flex',gap:12,alignItems:'center'}}>
            {appMode==='backtest'&&result&&<span style={{fontSize:'0.72rem',color:'#8b8b9d'}}>{result.metrics?.total_trades||0} trades · {config.years_history}y</span>}
            {appMode==='live'&&lastHeartbeat&&<span style={{fontSize:'0.72rem',color:'#8b8b9d'}}>HB: {lastHeartbeat.toLocaleTimeString()}</span>}
          </div>
        </header>

        <div className="content-body">
          {/* BACKTEST */}
          {appMode==='backtest'&&(
            <>
              {apiError&&<div style={{padding:'10px 16px',background:'rgba(245,101,101,0.1)',border:'1px solid #f56565',borderRadius:8,color:'#f56565',fontSize:'0.85rem'}}>⚠ {apiError}</div>}
              {result&&(
                <div className="metrics-grid">
                  <MetricCard title="Total P&L" value={`$${(m.total_pnl??0).toLocaleString()}`} color={(m.total_pnl??0)>=0?'#48bb78':'#f56565'}/>
                  <MetricCard title="Win Rate" value={`${m.win_rate??0}%`} color={(m.win_rate??0)>=50?'#48bb78':'#f56565'}/>
                  <MetricCard title="Total Trades" value={m.total_trades??0}/>
                  <MetricCard title="Sharpe Ratio" value={m.sharpe_ratio??0} color={(m.sharpe_ratio??0)>1?'#48bb78':(m.sharpe_ratio??0)>0?'#ecc94b':'#f56565'}/>
                  <MetricCard title="Sortino" value={m.sortino_ratio??0}/>
                  <MetricCard title="Max Drawdown" value={`${m.max_drawdown??0}%`} color="#f56565"/>
                  <MetricCard title="Profit Factor" value={m.profit_factor??0} color={(m.profit_factor??0)>=1.5?'#48bb78':(m.profit_factor??0)>=1?'#ecc94b':'#f56565'}/>
                  <MetricCard title="Kelly %" value={`${m.kelly_pct??0}%`}/>
                  <MetricCard title="Avg Hold (d)" value={m.avg_hold_days??0}/>
                  <MetricCard title="Recovery Factor" value={m.recovery_factor??0}/>
                  <MetricCard title="Avg Win" value={`$${m.avg_win??0}`} color="#48bb78"/>
                  <MetricCard title="Avg Loss" value={`-$${m.avg_loss??0}`} color="#f56565"/>
                  <MetricCard title="Max Consec Loss" value={m.max_consec_losses??0} color="#f56565"/>
                  <MetricCard title="Final Equity" value={`$${(m.final_equity??0).toLocaleString()}`} color={(m.total_pnl??0)>=0?'#48bb78':'#f56565'}/>
                </div>
              )}

              <div style={{display:'flex',gap:4,borderBottom:'1px solid var(--border)'}}>
                {['chart','trades','analytics','optimizer'].map(t=>(
                  <button key={t} onClick={()=>setActiveTab(t)} style={{padding:'9px 18px',background:'none',border:'none',borderBottom:activeTab===t?'2px solid var(--accent)':'2px solid transparent',color:activeTab===t?'#a78bfa':'#8b8b9d',fontWeight:600,fontSize:'0.82rem',cursor:'pointer',textTransform:'capitalize',transition:'all 0.2s'}}>{t}</button>
                ))}
              </div>

              {activeTab==='chart'&&(
                <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:16}}>
                  <div style={{background:'var(--bg-card)',borderRadius:12,border:'1px solid var(--border)',padding:16}}>
                    <h3 style={{marginBottom:12,display:'flex',alignItems:'center',gap:8,fontSize:'0.85rem',color:'#8b8b9d'}}><BarChart2 size={16}/>Price Action + Signals</h3>
                    <div ref={chartContainerRef} style={{width:'100%',height:'380px'}}/>
                  </div>
                  <div style={{background:'var(--bg-card)',borderRadius:12,border:'1px solid var(--border)',padding:16}}>
                    <h3 style={{marginBottom:12,display:'flex',alignItems:'center',gap:8,fontSize:'0.85rem',color:'#8b8b9d'}}><TrendingUp size={16}/>Equity Curve</h3>
                    {result?.equity_curve?(
                      <ResponsiveContainer width="100%" height={380}>
                        <AreaChart data={result.equity_curve}>
                          <XAxis dataKey="date" hide/><YAxis hide/>
                          <Tooltip contentStyle={{background:'var(--bg-panel)',border:'1px solid var(--border)',borderRadius:8,fontSize:'0.75rem'}} formatter={v=>[`$${Number(v).toLocaleString()}`,'Equity']}/>
                          <Area type="monotone" dataKey="equity" stroke="#a78bfa" fill="#6b46c1" fillOpacity={0.15}/>
                        </AreaChart>
                      </ResponsiveContainer>
                    ):<div style={{height:380,display:'flex',alignItems:'center',justifyContent:'center',color:'#8b8b9d'}}>Run simulation to see equity curve</div>}
                  </div>
                </div>
              )}

              {activeTab==='trades'&&(
                <div className="table-container"><div><table>
                  <thead><tr><th>Entry</th><th>Exit</th><th>SPY In</th><th>SPY Out</th><th>Cost</th><th>Exit Val</th><th>P&L</th><th>Cts</th><th>Days</th><th>Commission</th><th>Reason</th><th>Regime</th><th>Result</th></tr></thead>
                  <tbody>
                    {!result?.trades?.length&&<tr><td colSpan={13} style={{textAlign:'center',color:'#8b8b9d',padding:24}}>No trades — run simulation first</td></tr>}
                    {(result?.trades||[]).map((t,i)=>(
                      <tr key={i}>
                        <td>{t.entry_date}</td><td>{t.exit_date}</td>
                        <td>${t.entry_spy}</td><td>${t.exit_spy}</td>
                        <td>${t.spread_cost.toFixed(0)}</td><td>${t.spread_exit.toFixed(0)}</td>
                        <td style={{color:t.pnl>=0?'#48bb78':'#f56565',fontWeight:600}}>{t.pnl>=0?'+':''}${t.pnl.toFixed(2)}</td>
                        <td>{t.contracts}</td><td>{t.days_held}d</td>
                        <td style={{color:'#8b8b9d'}}>${t.commission.toFixed(2)}</td>
                        <td><span style={{fontSize:'0.7rem',color:'#8b8b9d'}}>{t.reason}</span></td>
                        <td><span style={{fontSize:'0.7rem',color:t.regime==='bull'?'#48bb78':t.regime==='bear'?'#f56565':'#ecc94b'}}>{t.regime}</span></td>
                        <td><span className={`badge ${t.win?'win':t.stopped_out?'stopped':'loss'}`}>{t.stopped_out?'STOP':t.win?'WIN':'LOSS'}</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table></div></div>
              )}

              {activeTab==='analytics'&&result&&(
                <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:16}}>
                  {result.duration_dist?.length>0&&(
                    <div style={{background:'var(--bg-card)',borderRadius:12,border:'1px solid var(--border)',padding:16}}>
                      <h3 style={{marginBottom:12,fontSize:'0.85rem',color:'#8b8b9d'}}>Duration Distribution</h3>
                      <ResponsiveContainer width="100%" height={200}>
                        <BarChart data={result.duration_dist}>
                          <XAxis dataKey="range" tick={{fontSize:10,fill:'#8b8b9d'}}/><YAxis hide/>
                          <Tooltip contentStyle={{background:'var(--bg-panel)',border:'1px solid var(--border)',borderRadius:8,fontSize:'0.75rem'}}/>
                          <Bar dataKey="count" fill="#6b46c1" radius={[4,4,0,0]}/>
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  )}
                  {result.monte_carlo?.distribution?.length>0&&(
                    <div style={{background:'var(--bg-card)',borderRadius:12,border:'1px solid var(--border)',padding:16}}>
                      <h3 style={{marginBottom:6,fontSize:'0.85rem',color:'#8b8b9d'}}>Monte Carlo (1,000 sim)</h3>
                      <div style={{display:'flex',gap:14,marginBottom:10,flexWrap:'wrap'}}>
                        {[['P5',result.monte_carlo.p05,'#f56565'],['P50',result.monte_carlo.p50,'#a78bfa'],['P95',result.monte_carlo.p95,'#48bb78'],['P(Profit)',`${result.monte_carlo.prob_profit}%`,'#48bb78']].map(([l,v,c])=>(
                          <span key={l} style={{fontSize:'0.72rem',color:'#8b8b9d'}}>{l}: <strong style={{color:c}}>{typeof v==='number'?`$${v.toLocaleString()}`:v}</strong></span>
                        ))}
                      </div>
                      <ResponsiveContainer width="100%" height={180}>
                        <BarChart data={result.monte_carlo.distribution}>
                          <XAxis dataKey="bin" tick={{fontSize:9,fill:'#8b8b9d'}} tickFormatter={v=>`$${(v/1000).toFixed(0)}k`}/><YAxis hide/>
                          <Tooltip contentStyle={{background:'var(--bg-panel)',border:'1px solid var(--border)',borderRadius:8,fontSize:'0.75rem'}}/>
                          <Bar dataKey="count" radius={[2,2,0,0]}>
                            {result.monte_carlo.distribution.map((e,idx)=><Cell key={idx} fill={e.profitable?'#48bb78':'#f56565'} fillOpacity={0.7}/>)}
                          </Bar>
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  )}
                  {result.regime_stats&&Object.keys(result.regime_stats).length>0&&(
                    <div style={{background:'var(--bg-card)',borderRadius:12,border:'1px solid var(--border)',padding:16}}>
                      <h3 style={{marginBottom:12,fontSize:'0.85rem',color:'#8b8b9d'}}>Regime Breakdown</h3>
                      {Object.entries(result.regime_stats).map(([regime,stats])=>(
                        <div key={regime} style={{display:'flex',justifyContent:'space-between',alignItems:'center',padding:'10px 0',borderBottom:'1px solid var(--border)'}}>
                          <span style={{textTransform:'capitalize',fontWeight:600,color:regime==='bull'?'#48bb78':regime==='bear'?'#f56565':'#ecc94b'}}>{regime}</span>
                          <div style={{display:'flex',gap:20,fontSize:'0.8rem',color:'#8b8b9d'}}>
                            <span>{stats.trades} trades</span>
                            <span style={{color:stats.win_rate>=50?'#48bb78':'#f56565'}}>{stats.win_rate}% WR</span>
                            <span style={{color:stats.pnl>=0?'#48bb78':'#f56565',fontWeight:600}}>${stats.pnl.toFixed(0)}</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                  {result.walk_forward?.length>0&&(
                    <div style={{background:'var(--bg-card)',borderRadius:12,border:'1px solid var(--border)',padding:16}}>
                      <h3 style={{marginBottom:12,fontSize:'0.85rem',color:'#8b8b9d'}}>Walk-Forward Analysis</h3>
                      {result.walk_forward.map(w=>(
                        <div key={w.window} style={{display:'flex',justifyContent:'space-between',padding:'8px 0',borderBottom:'1px solid var(--border)',fontSize:'0.8rem'}}>
                          <span style={{color:'#8b8b9d'}}>W{w.window} <span style={{fontSize:'0.7rem'}}>{w.start_date}→{w.end_date}</span></span>
                          <div style={{display:'flex',gap:16}}>
                            <span>{w.trades}t</span>
                            <span style={{color:w.win_rate>=50?'#48bb78':'#f56565'}}>{w.win_rate}%</span>
                            <span style={{color:w.pnl>=0?'#48bb78':'#f56565',fontWeight:600}}>${w.pnl.toFixed(0)}</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {activeTab==='optimizer'&&(
                <div style={{background:'var(--bg-card)',borderRadius:12,border:'1px solid var(--border)',padding:20}}>
                  <h3 style={{marginBottom:16,fontSize:'0.9rem',color:'#8b8b9d',display:'flex',alignItems:'center',gap:8}}><Zap size={16}/>Parameter Optimizer</h3>
                  <div style={{display:'grid',gridTemplateColumns:'1fr 1fr auto',gap:12,marginBottom:16}}>
                    {[['X Parameter',optParamX,setOptParamX],['Y Parameter',optParamY,setOptParamY]].map(([lbl,val,setter])=>(
                      <Field key={lbl} label={lbl}>
                        <select value={val} onChange={e=>setter(e.target.value)} style={{width:'100%',background:'var(--bg-dark)',color:'#fff',border:'1px solid var(--border)',borderRadius:8,padding:8}}>
                          <option value="entry_red_days">Red Days</option>
                          <option value="target_dte">Target DTE</option>
                          <option value="stop_loss_pct">Stop Loss %</option>
                          <option value="rsi_threshold">RSI Threshold</option>
                        </select>
                      </Field>
                    ))}
                    <div style={{display:'flex',alignItems:'flex-end'}}>
                      <button className="btn-primary" onClick={runOptimizer} disabled={optimizing} style={{width:'auto',padding:'8px 24px',marginTop:0}}>
                        {optimizing?'Running…':'Optimize'}
                      </button>
                    </div>
                  </div>
                  {optimizerResult?.results&&(
                    <div style={{overflowX:'auto'}}><table>
                      <thead><tr><th>{optParamX}</th><th>{optParamY}</th><th>Trades</th><th>Win Rate</th><th>Total P&L</th></tr></thead>
                      <tbody>
                        {[...optimizerResult.results].sort((a,b)=>b.pnl-a.pnl).map((r,i)=>(
                          <tr key={i} style={{background:i===0?'rgba(72,187,120,0.05)':undefined}}>
                            <td>{r.x}</td><td>{r.y}</td><td>{r.trades}</td>
                            <td style={{color:r.win_rate>=50?'#48bb78':'#f56565'}}>{r.win_rate}%</td>
                            <td style={{color:r.pnl>=0?'#48bb78':'#f56565',fontWeight:600}}>{i===0&&'★ '}${r.pnl.toFixed(0)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table></div>
                  )}
                </div>
              )}
            </>
          )}

          {appMode==='paper'&&(
            <PaperPanel
              paperKey={paperKey} setPaperKey={setPaperKey}
              paperSecret={paperSecret} setPaperSecret={setPaperSecret}
              paperAccount={paperAccount} paperConnecting={paperConnecting}
              connectPaper={connectPaper}
              paperPositions={paperPositions} paperOrders={paperOrders}
              paperSignal={paperSignal} scanLog={scanLog}
              paperScanning={paperScanning}
              paperAutoExec={paperAutoExec} setPaperAutoExec={setPaperAutoExec}
              scanTimingMode={scanTimingMode} setScanTimingMode={setScanTimingMode}
              scanTimingValue={scanTimingValue} setScanTimingValue={setScanTimingValue}
              toggleScanning={togglePaperScanning}
              manualScan={()=>scanSignal(false)}
              killSwitch={paperKillSwitch}
              msg={paperMsg}
              refreshData={loadPaperData}
              spyData={spyData}
              tradePresets={tradePresets}
              onSavePreset={saveTradePreset}
              onLoadPreset={loadTradePreset}
              onDeletePreset={deleteTradePreset}
            />
          )}

          {appMode==='live'&&(
            <LivePanel
              ibkrHost={ibkrHost} setIbkrHost={setIbkrHost}
              ibkrPort={ibkrPort} setIbkrPort={setIbkrPort}
              ibkrClientId={ibkrClientId} setIbkrClientId={setIbkrClientId}
              ibkrAccount={ibkrAccount} ibkrConnecting={ibkrConnecting}
              connStatus={connStatus} connectIbkr={connectIbkr}
              reconnectIbkr={reconnectIbkr}
              ibkrPositions={ibkrPositions} ibkrOrders={ibkrOrders}
              msg={ibkrMsg} setMsg={setIbkrMsg}
              killSwitch={ibkrKillSwitch}
              placeTestOrder={placeIbkrTestOrder}
              refreshData={loadIbkrData}
              ibkrScanning={ibkrScanning}
              toggleScanning={toggleIbkrScanning}
              ibkrScanLog={ibkrScanLog}
              ibkrScanSignal={ibkrScanSignal}
              ibkrScanTimingMode={ibkrScanTimingMode} setIbkrScanTimingMode={setIbkrScanTimingMode}
              ibkrScanTimingValue={ibkrScanTimingValue} setIbkrScanTimingValue={setIbkrScanTimingValue}
              spyData={spyData}
              tradePresets={tradePresets}
              onSavePreset={saveTradePreset}
              onLoadPreset={loadTradePreset}
              onDeletePreset={deleteTradePreset}
            />
          )}
        </div>
      </div>
    </div>
  );
}
