"""
title: AI 合同审核（Dify 测试版）
author: Local AI Legal Project
version: 1.0.0
required_open_webui_version: 0.11.3
requirements: httpx
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field


FORM_CODE = r"""
return new Promise((resolve) => {
  try {
    const old = document.getElementById('ai-legal-review-form');
    if (old) old.remove();
    const dark = document.documentElement.classList.contains('dark');
    const overlay = document.createElement('div');
    overlay.id = 'ai-legal-review-form';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(15,23,42,.62);display:flex;align-items:center;justify-content:center;z-index:99999;padding:20px';
    const panel = document.createElement('div');
    panel.style.cssText = `width:min(620px,96vw);max-height:90vh;overflow:auto;background:${dark?'#111827':'#fff'};color:${dark?'#f8fafc':'#172033'};border-radius:18px;padding:24px;box-shadow:0 24px 80px rgba(0,0,0,.3)`;
    panel.innerHTML = `
      <h2 style="font-size:22px;font-weight:700;margin:0 0 6px">AI 合同审核要求</h2>
      <p style="margin:0 0 20px;color:${dark?'#cbd5e1':'#64748b'}">请选择本次审核参数。高风险事项仍须法务人工复核。</p>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
        <label style="display:grid;gap:6px">合同类型<select id="legal-type"><option>自动识别</option><option>采购合同</option><option>销售合同</option><option>软件开发/服务合同</option><option>保密协议</option><option>劳动用工文件</option><option>其他合同</option></select></label>
        <label style="display:grid;gap:6px">所属行业<select id="legal-industry"><option>通用</option><option>互联网与软件</option><option>制造业</option><option>金融</option><option>零售与电商</option><option>医疗健康</option><option>教育</option></select></label>
        <label style="display:grid;gap:6px">适用法律<select id="legal-jurisdiction"><option>中国大陆</option><option>中国香港</option><option>新加坡</option><option>欧盟</option><option>美国</option><option>其他/待确认</option></select></label>
        <label style="display:grid;gap:6px">审核严格程度<select id="legal-strictness"><option>标准</option><option>严格</option><option>宽松</option></select></label>
      </div>
      <fieldset style="border:1px solid ${dark?'#334155':'#dbe3ee'};border-radius:12px;margin:18px 0;padding:14px"><legend style="padding:0 7px">重点风险类型</legend>
        <div id="legal-focus" style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px">
          ${['合同权利义务','违约责任','数据与隐私','劳动用工','知识产权','反腐败与反洗钱','广告与消费者权益','跨境合规'].map((x,i)=>`<label><input type="checkbox" value="${x}" ${i<3?'checked':''}/> ${x}</label>`).join('')}
        </div>
      </fieldset>
      <div style="display:flex;justify-content:flex-end;gap:10px">
        <button id="legal-cancel" style="padding:9px 16px;border:1px solid #cbd5e1;border-radius:9px;background:transparent;color:inherit">取消</button>
        <button id="legal-submit" style="padding:9px 18px;border:0;border-radius:9px;background:#2563eb;color:white;font-weight:650">开始审核</button>
      </div>`;
    panel.querySelectorAll('select').forEach((el) => el.style.cssText=`width:100%;padding:9px;border-radius:8px;border:1px solid ${dark?'#475569':'#cbd5e1'};background:${dark?'#1f2937':'#fff'};color:inherit`);
    overlay.appendChild(panel);
    document.body.appendChild(overlay);
    const finish = (value) => { overlay.remove(); resolve(value); };
    panel.querySelector('#legal-cancel').onclick = () => finish({cancelled:true});
    panel.querySelector('#legal-submit').onclick = () => finish({
      declared_doc_type: panel.querySelector('#legal-type').value,
      industry: panel.querySelector('#legal-industry').value,
      jurisdiction: panel.querySelector('#legal-jurisdiction').value,
      strictness: panel.querySelector('#legal-strictness').value,
      focus_risk_types: [...panel.querySelectorAll('#legal-focus input:checked')].map(x=>x.value).join('、') || '全部'
    });
  } catch (error) {
    resolve({error:String(error)});
  }
});
"""


class Pipe:
    class Valves(BaseModel):
        gateway_url: str = Field(
            default="http://contract-review-gateway:8787",
            description="容器内部合同审核网关地址",
        )
        poll_seconds: float = Field(default=2.0, ge=0.5, le=10.0)
        timeout_seconds: int = Field(default=360, ge=30, le=900)

    def __init__(self):
        self.valves = self.Valves()

    async def _status(self, emitter, text: str, done: bool = False) -> None:
        if emitter:
            await emitter(
                {
                    "type": "status",
                    "data": {"description": text, "done": done, "hidden": False},
                }
            )

    async def pipe(
        self,
        body: dict,
        __user__: dict | None = None,
        __files__: list[dict] | None = None,
        __event_emitter__=None,
        __event_call__=None,
    ) -> str:
        files = [item for item in (__files__ or []) if item.get("id")]
        if not files:
            return (
                "请先点击输入框旁的附件按钮，上传一个 PDF、DOCX 或 TXT 合同，"
                "然后发送“开始审核”。"
            )
        selected = files[-1]
        name = str(selected.get("name") or selected.get("filename") or "contract")
        if Path(name).suffix.lower() not in {".pdf", ".docx", ".txt"}:
            return "文件格式不支持。请上传 PDF、DOCX 或 TXT 文件。"

        options: dict[str, Any] = {
            "declared_doc_type": "自动识别",
            "industry": "通用",
            "jurisdiction": "中国大陆",
            "strictness": "标准",
            "focus_risk_types": "全部",
        }
        if __event_call__:
            response = await __event_call__(
                {"type": "execute", "data": {"code": FORM_CODE}}
            )
            if isinstance(response, dict) and response.get("cancelled"):
                return "本次审核已取消，合同没有发送到 Dify。"
            if isinstance(response, dict) and response.get("error"):
                return "无法打开审核选项窗口，请刷新页面后重试。"
            if isinstance(response, dict):
                options.update(
                    {key: value for key, value in response.items() if key in options}
                )

        await self._status(__event_emitter__, "正在读取已上传的合同文件")
        try:
            from open_webui.models.files import Files
            from open_webui.storage.provider import Storage

            user_id = str((__user__ or {}).get("id") or "local-user")
            file_model = await Files.get_file_by_id_and_user_id(selected["id"], user_id)
            if not file_model:
                return "无法读取该合同文件，请删除附件后重新上传。"
            local_path = Path(Storage.get_file(file_model.path))
            if not local_path.is_file():
                return "合同文件在本地存储中不存在，请重新上传。"

            await self._status(__event_emitter__, "合同上传完成，正在启动 Dify 审核")
            timeout = httpx.Timeout(self.valves.timeout_seconds, connect=15.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                with local_path.open("rb") as handle:
                    create = await client.post(
                        f"{self.valves.gateway_url.rstrip('/')}/api/reviews",
                        data={**options, "local_user_id": user_id},
                        files={
                            "file": (
                                name,
                                handle,
                                selected.get("content_type") or "application/octet-stream",
                            )
                        },
                    )
                if create.status_code >= 400:
                    detail = create.json().get("detail", "上传失败")
                    return f"合同上传失败：{detail}"
                job = create.json()
                job_id = job["job_id"]
                previous = ""
                for _ in range(int(self.valves.timeout_seconds / self.valves.poll_seconds)):
                    check = await client.get(
                        f"{self.valves.gateway_url.rstrip('/')}/api/reviews/{job_id}"
                    )
                    check.raise_for_status()
                    job = check.json()
                    message = str(job.get("message") or "正在审核")
                    if message != previous:
                        await self._status(__event_emitter__, message)
                        previous = message
                    if job.get("status") == "completed":
                        return await self._render_result(job, __event_emitter__)
                    if job.get("status") == "failed":
                        await self._status(__event_emitter__, message, done=True)
                        return f"审核失败：{message}"
                    await asyncio.sleep(self.valves.poll_seconds)
        except httpx.TimeoutException:
            return "审核超时。合同可能较长，请稍后重试。"
        except httpx.ConnectError:
            return "无法连接本地合同审核服务，请先运行“一键启动”。"
        except Exception:
            return "审核过程中发生本地错误，请查看服务状态后重试。"
        finally:
            await self._status(__event_emitter__, "本次处理已结束", done=True)
        return "审核超时。合同可能较长，请稍后重试。"

    async def _render_result(self, job: dict, emitter) -> str:
        result = job.get("result") or {}
        markdown = str(result.get("report_markdown") or "").strip()
        high = int(result.get("high_risk_count") or 0)
        manual = bool(result.get("manual_review_required"))
        mode = str(job.get("mode") or "live")
        lines = []
        if mode != "live":
            lines.append(
                "> ⚠️ 当前是本地模拟联调结果，不代表 Dify 已完成真实审核。\n"
            )
        lines.append(f"> 高风险事项：**{high}** 项。")
        if manual:
            lines.append("> ⚠️ 报告包含必须由法务人工确认的内容。")
        lines.extend(["", markdown, "", "## 下载审核报告", ""])
        downloads = result.get("downloads") or {}
        if downloads.get("docx"):
            lines.append(f"- [下载 Word 审核报告]({downloads['docx']})")
        if downloads.get("pdf"):
            lines.append(f"- [下载 PDF 审核报告]({downloads['pdf']})")
        if not downloads:
            lines.append("- 本次没有生成可下载文件，请人工检查 Dify 输出节点。")
        await self._status(emitter, "审核完成，可以查看并下载报告", done=True)
        return "\n".join(lines)
