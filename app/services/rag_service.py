"""
app/services/rag_service.py — RAG Pipeline Service.

Extracted and adapted from poc_backend.py. Preserves the exact 6-step pipeline:
  1. Query Intelligence (LLM analyzes, extracts entities, generates variants + HyDE)
  2. Multi-Vector Search (each variant + HyDE → embedding → ChromaDB)
  3. Reciprocal Rank Fusion (merge results across queries)
  4. AI Relevance Judge (LLM ranks by semantic relevance)
  5. Relevance Filtering (keep only EXACT/LIKELY/POSSIBLE)
  6. Answer Synthesis (format context + generate final answer)

All blocking LLM/embedding calls run via asyncio.to_thread().
"""

import asyncio
import json
import logging
import re
from collections import defaultdict
from typing import Callable, Optional

from app.config import settings
from app.utils.prompts import SYSTEM_PROMPT, QUERY_INTELLIGENCE_PROMPT, RELEVANCE_JUDGE_PROMPT, CLARIFICATION_PROMPT
from app.utils.llm_client import call_llm_sync, parse_json_response
from app.utils.embedding import get_embedding

log = logging.getLogger(__name__)


class RAGService:
    """
    Stateless RAG pipeline service.

    Each call to run() executes the full pipeline. Stage callbacks are invoked
    for WebSocket progress reporting.
    """

    def __init__(self, chroma_collection, stage_callback: Optional[Callable] = None):
        """
        Args:
            chroma_collection: ChromaDB collection instance.
            stage_callback: async callable(stage: str) for progress reporting.
        """
        self.collection = chroma_collection
        self.stage_callback = stage_callback

    # ── Low-level helpers (synchronous — run in thread pool) ──────────────

    def _single_search(self, query: str, top_k: int, task_type: str = "search_query") -> list[dict]:
        """Single query embedding → ChromaDB search."""
        if self.collection is None:
            raise RuntimeError("ChromaDB not available")

        embedding = get_embedding(query, task_type=task_type)
        results = self.collection.query(
            query_embeddings=[embedding],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )

        docs = results["documents"][0]
        metas = results["metadatas"][0]
        distances = results["distances"][0]

        hits = []
        for doc, meta, dist in zip(docs, metas, distances):
            pdf_name = meta.get("pdf_name", "") or meta.get("bridge_number", "") or meta.get("filename", "")
            brand = meta.get("brand", "")
            hits.append({
                "text": doc,
                "similarity": round(1 - dist, 4),
                "distance": round(dist, 4),
                "part_number": meta.get("part_number", ""),
                "description": meta.get("description", ""),
                "document_id": meta.get("document_id", ""),
                "page_number": meta.get("page_number", ""),
                "planche": meta.get("planche", ""),
                "planche_title": meta.get("planche_title", ""),
                "model_ref": meta.get("model_ref", ""),
                "filename": pdf_name,
                "brand": brand,
            })
        return hits

    def _analyze_query_intelligence(self, user_query: str, context: dict, history: list[dict]) -> dict:
        """Step 1: Query Intelligence — LLM analyzes the query."""
        context_note = ""
        if context:
            parts = []
            for k, v in context.items():
                if isinstance(v, list):
                    parts.append(f"{k}: {', '.join(str(x) for x in v)}")
                else:
                    parts.append(f"{k}: {v}")
            context_note = f"\n\nMár ismert session kontextus: {' | '.join(parts)}"

        history_note = ""
        if history:
            last_turns = history[-6:]
            history_note = "\n\nKorábbi beszélgetés:\n" + "\n".join(
                f"  {m['role']}: {m['content'][:150]}" for m in last_turns
            )

        prompt = (
            f'FELHASZNÁLÓ KERESÉSE: "{user_query}"'
            f"{context_note}"
            f"{history_note}"
        )

        try:
            raw = call_llm_sync(
                messages=[
                    {"role": "system", "content": QUERY_INTELLIGENCE_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=800,
                temperature=0.15,
            )
            result = parse_json_response(raw)

            if "search_queries" not in result:
                result["search_queries"] = [user_query]
            if "hyde_document" not in result:
                result["hyde_document"] = user_query
            if "extracted_entities" not in result:
                result["extracted_entities"] = {}

            return result
        except Exception as e:
            log.warning(f"Query Intelligence failed: {e} — fallback to raw query")
            return {
                "original_query": user_query,
                "is_specific": False,
                "extracted_entities": {},
                "search_queries": [user_query],
                "hyde_document": user_query,
            }

    def _multi_query_fused_search(self, search_queries: list[str], hyde_doc: str,
                                   top_k: int = None) -> list[dict]:
        """Step 2+3: Multi-query search + Reciprocal Rank Fusion."""
        if top_k is None:
            top_k = settings.TOP_K

        all_hits: dict[str, dict] = {}
        rrf_scores: dict[str, float] = {}
        k = 60  # RRF constant

        search_items = []
        for q in search_queries:
            search_items.append((q, "search_query"))

        if hyde_doc and hyde_doc != search_queries[0]:
            search_items.append((hyde_doc, "search_document"))

        for query_text, task_type in search_items:
            try:
                hits = self._single_search(query_text, top_k=top_k, task_type=task_type)
            except Exception as e:
                log.warning(f"Search failed: {e}")
                continue

            for rank, hit in enumerate(hits):
                key = f"{hit['part_number']}|{hit['document_id']}|{hit['page_number']}"
                rrf_score = 1.0 / (k + rank + 1)
                rrf_scores[key] = rrf_scores.get(key, 0.0) + rrf_score

                if key not in all_hits:
                    all_hits[key] = hit
                    all_hits[key]["_rrf_score"] = 0.0
                    all_hits[key]["_hit_count"] = 0

                all_hits[key]["_rrf_score"] = rrf_scores[key]
                all_hits[key]["_hit_count"] += 1

        sorted_keys = sorted(rrf_scores.keys(), key=lambda k_: rrf_scores[k_], reverse=True)

        fused = []
        for key in sorted_keys[:settings.FUSION_TOP_K]:
            hit = all_hits[key]
            hit["rrf_score"] = round(rrf_scores[key], 4)
            fused.append(hit)

        return fused

    def _ai_relevance_judge(self, user_query: str, hits: list[dict],
                             context: dict, max_to_judge: int = 20) -> dict:
        """Step 4: AI Relevance Judge — LLM evaluates search results."""
        if not hits:
            return {
                "overall_assessment": "Nincsenek találatok.",
                "needs_clarification": True,
                "clarification_reason": "Nincs találat az adatbázisban.",
                "ranked_results": [],
            }

        hits_to_judge = hits[:max_to_judge]

        results_text_parts = []
        for i, h in enumerate(hits_to_judge, 1):
            results_text_parts.append(
                f"[{i}] "
                f"Cikkszám: {h['part_number']} | "
                f"Leírás: {h['description']} | "
                f"Szekció: {h.get('planche_title', '')} ({h.get('planche', '')}) | "
                f"Modell ref: {h.get('model_ref', '')} | "
                f"Dokumentum: {h.get('filename', '')} old. {h['page_number']} | "
                f"Vektoros hasonlóság: {h.get('similarity', 0):.2f} | "
                f"RRF score: {h.get('rrf_score', 0):.4f}"
            )

        results_block = "\n".join(results_text_parts)

        context_note = ""
        if context:
            parts = []
            for k, v in context.items():
                if isinstance(v, list):
                    parts.append(f"{k}: {', '.join(str(x) for x in v)}")
                else:
                    parts.append(f"{k}: {v}")
            context_note = f"\nSession kontextus: {' | '.join(parts)}"

        prompt = (
            f'EREDETI KERESÉS: "{user_query}"'
            f"{context_note}\n\n"
            f"VEKTOROS KERESÉS TALÁLATAI (RRF fúzionált, {len(hits_to_judge)} db):\n"
            f"{results_block}"
        )

        try:
            raw = call_llm_sync(
                messages=[
                    {"role": "system", "content": RELEVANCE_JUDGE_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=1200,
                temperature=0.15,
            )
            result = parse_json_response(raw)

            if "ranked_results" not in result:
                result["ranked_results"] = []
            if "needs_clarification" not in result:
                result["needs_clarification"] = False

            # Update hit objects with AI relevance labels
            judge_map = {}
            for judged in result.get("ranked_results", []):
                idx = judged.get("index", 0) - 1
                if 0 <= idx < len(hits_to_judge):
                    judge_map[idx] = judged

            for i, h in enumerate(hits_to_judge):
                if i in judge_map:
                    h["ai_relevance"] = judge_map[i].get("relevance", "UNKNOWN")
                    h["ai_confidence"] = judge_map[i].get("confidence", 0.0)
                    h["ai_reason"] = judge_map[i].get("reason", "")
                else:
                    h["ai_relevance"] = "UNKNOWN"
                    h["ai_confidence"] = 0.0
                    h["ai_reason"] = ""

            return result
        except Exception as e:
            log.warning(f"AI Judge failed: {e} — fallback to vector order")
            return {
                "overall_assessment": "AI bíró nem elérhető, vektoros hasonlóság alapján rendezve.",
                "needs_clarification": False,
                "clarification_reason": "",
                "ranked_results": [],
            }

    def _filter_relevant_hits(self, hits: list[dict], judge_result: dict) -> list[dict]:
        """Step 5: Filter hits based on AI judge verdict."""
        if not judge_result.get("ranked_results"):
            return [h for h in hits if h.get("similarity", 0) > 0.35]

        relevant = []
        judge_map = {}
        for judged in judge_result.get("ranked_results", []):
            idx = judged.get("index", 0) - 1
            if 0 <= idx < len(hits):
                judge_map[idx] = judged.get("relevance", "UNKNOWN")

        for i, h in enumerate(hits):
            relevance = judge_map.get(i, "UNKNOWN")
            if relevance in ("EXACT", "LIKELY", "POSSIBLE"):
                relevant.append(h)

        if not relevant:
            relevant = hits[:3]

        return relevant

    def _format_context_for_llm(self, hits: list[dict]) -> str:
        """Format search results as context for the final LLM call."""
        lines = []
        for i, h in enumerate(hits, 1):
            ai_label = h.get("ai_relevance", "")
            ai_conf = h.get("ai_confidence", 0.0)
            label_str = f" [AI: {ai_label} {ai_conf:.0%}]" if ai_label else ""

            lines.append(
                f"[{i}]{label_str} "
                f"Cikkszám: {h['part_number']} | "
                f"Leírás: {h['description']} | "
                f"Szekció: {h.get('planche_title', '')} ({h.get('planche', '')}) | "
                f"Modell: {h.get('model_ref', '')} | "
                f"Dok: {h.get('filename', '')} oldal {h['page_number']} | "
                f"Egyezés: {h.get('similarity', 0):.2f}"
            )
        return "\n".join(lines)

    def _extract_context_from_answer(self, user_msg: str, context: dict):
        """Extract structured context (machine type, dimensions, etc.) from user message."""
        msg_lower = user_msg.lower()

        carraro_match = re.search(r'carraro\s+(\d+[-/]\d+)', msg_lower)
        if carraro_match:
            context["machine_type"] = f"Carraro {carraro_match.group(1)}"

        mod_match = re.search(r'(mod\.?\s*n?\.?\s*[\d/-]+)', msg_lower)
        if mod_match:
            context["model_ref"] = mod_match.group(1).upper()

        for keyword in ["traktor", "tractor", "kombájn", "harvester", "dömper",
                         "loader", "kotró", "fúró", "maró"]:
            if keyword in msg_lower:
                context["machine_type"] = keyword

        size_match = re.search(r'(\d+\.?\d*\s*mm|\bM\d+x\d+\b)', msg_lower, re.IGNORECASE)
        if size_match:
            context["dimensions"] = size_match.group(1)

    def _build_sources_payload(self, hits: list[dict]) -> list[dict]:
        """Build the sources array for the API response.
        
        Includes brand/code/page fields for frontend PDF viewer compatibility.
        """
        # Sort by similarity descending (highest match first)
        sorted_hits = sorted(hits, key=lambda h: h.get("similarity", 0), reverse=True)
        result = []
        for h in sorted_hits[:15]:
            filename = h.get("filename", "")
            brand = h.get("brand", "")
            # Strip .pdf extension for the 'code' field
            code = filename
            if code.lower().endswith(".pdf"):
                code = code[:-4]
            result.append({
                "part_number": h["part_number"],
                "description": h["description"],
                "similarity": h.get("similarity", 0),
                "rrf_score": h.get("rrf_score", 0),
                "ai_relevance": h.get("ai_relevance", ""),
                "ai_confidence": h.get("ai_confidence", 0),
                "ai_reason": h.get("ai_reason", ""),
                "document_id": h["document_id"],
                "page_number": h["page_number"],
                "planche": h.get("planche", ""),
                "planche_title": h.get("planche_title", ""),
                "model_ref": h.get("model_ref", ""),
                "filename": filename,
                "brand": brand,
                "code": code,
                "page": str(h.get("page_number", "")),
            })
        return result

    def _generate_clarification(
        self, user_query: str, context: dict, reason: str,
        result_count: int = 0, hits_summary: str = "",
    ) -> str:
        """Generate AI-powered contextual clarification questions."""
        context_info = ""
        if context:
            parts = []
            for k, v in context.items():
                if isinstance(v, list):
                    parts.append(f"{k}: {', '.join(str(x) for x in v)}")
                else:
                    parts.append(f"{k}: {v}")
            context_info = " | ".join(parts) if parts else ""

        prompt = (
            f"FELHASZNÁLÓ KERESÉSE: \"{user_query}\"\n"
            f"VISSZAKÉRDEZÉS OKA: {reason}\n"
        )
        if context_info:
            prompt += f"KINYERT INFORMÁCIÓK: {context_info}\n"
        if result_count > 0:
            prompt += f"KERESÉSI TALÁLATOK SZÁMA: {result_count}\n"
        if hits_summary:
            prompt += f"TALÁLATOK ÖSSZEFOGLALÓJA: {hits_summary}\n"

        try:
            return call_llm_sync(
                messages=[
                    {"role": "system", "content": CLARIFICATION_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=400,
                temperature=0.3,
            )
        except Exception as e:
            log.warning(f"Clarification generation failed: {e}")
            return "Kérlek pontosítsd a keresést a jobb találatok érdekében."

    # ── Public API ────────────────────────────────────────────────────────

    async def run(self, user_query: str, history: list[dict],
                  context: Optional[dict] = None) -> dict:
        """
        Execute the full RAG pipeline.

        Args:
            user_query: The user's search query.
            history: Previous conversation turns [{role, content}, ...].
            context: Accumulated session context dict.

        Returns:
            {
                "answer": str,
                "sources": list[dict],
                "needs_clarification": bool,
                "clarification_text": str | None,
                "search_queries": list[str],
                "entities": dict,
            }
        """
        if context is None:
            context = {}

        # Extract context from the user message
        self._extract_context_from_answer(user_query, context)

        # ── Step 1: Query Intelligence ────────────────────────────────
        if self.stage_callback:
            await self.stage_callback("searching")

        qi_result = await asyncio.to_thread(
            self._analyze_query_intelligence, user_query, context, history
        )
        search_queries = qi_result.get("search_queries", [user_query])
        hyde_doc = qi_result.get("hyde_document", user_query)
        extracted = qi_result.get("extracted_entities", {})

        # Update context with extracted entities
        if extracted.get("machine_types"):
            context["machine_type"] = extracted["machine_types"][0]
        if extracted.get("categories"):
            context["part_category"] = extracted["categories"][0]
        if extracted.get("part_numbers"):
            context["part_numbers"] = list(set(
                context.get("part_numbers", []) + extracted["part_numbers"]
            ))
        if extracted.get("dimensions"):
            context["dimensions"] = extracted["dimensions"][0]

        # Ambiguity check
        is_specific = qi_result.get("is_specific", True)
        has_entities = bool(
            extracted.get("part_numbers") or
            extracted.get("dimensions") or
            extracted.get("machine_types")
        )

        if not is_specific and not has_entities:
            if self.stage_callback:
                await self.stage_callback("analyzing")
            clarification_text = await asyncio.to_thread(
                self._generate_clarification,
                user_query, context,
                "A keresés túl általános, nincs elég konkrét információ (pl. cikkszám, méret, géptípus).",
            )
            if self.stage_callback:
                await self.stage_callback("done")
            return {
                "answer": clarification_text,
                "sources": [],
                "needs_clarification": True,
                "clarification_text": clarification_text,
                "search_queries": search_queries,
                "entities": extracted,
            }

        # ── Step 2+3: Multi-Vector Search + RRF ───────────────────────
        if self.stage_callback:
            await self.stage_callback("analyzing")

        fused_hits = await asyncio.to_thread(
            self._multi_query_fused_search, search_queries, hyde_doc
        )

        if not fused_hits:
            if self.stage_callback:
                await self.stage_callback("refining")
            clarification_text = await asyncio.to_thread(
                self._generate_clarification,
                user_query, context,
                "Nincs találat az adatbázisban. Lehet, hogy más márka, más elnevezés vagy más cikkszám alatt szerepel.",
            )
            if self.stage_callback:
                await self.stage_callback("done")
            return {
                "answer": clarification_text,
                "sources": [],
                "needs_clarification": True,
                "clarification_text": clarification_text,
                "search_queries": search_queries,
                "entities": extracted,
            }

        # ── Step 4: AI Relevance Judge ────────────────────────────────
        if self.stage_callback:
            await self.stage_callback("refining")

        judge_result = await asyncio.to_thread(
            self._ai_relevance_judge, user_query, fused_hits, context
        )

        if judge_result.get("needs_clarification"):
            reason = judge_result.get("clarification_reason", "Túl sok bizonytalan találat.")
            hits_summary = f"{len(fused_hits)} találat, de egyik sem egyértelműen azonosítható."
            if self.stage_callback:
                await self.stage_callback("generating")
            clarification_text = await asyncio.to_thread(
                self._generate_clarification,
                user_query, context, reason,
                result_count=len(fused_hits),
                hits_summary=hits_summary,
            )
            if self.stage_callback:
                await self.stage_callback("done")
            return {
                "answer": clarification_text,
                "sources": [],
                "needs_clarification": True,
                "clarification_text": clarification_text,
                "search_queries": search_queries,
                "entities": extracted,
            }

        # ── Step 5: Filter relevant hits ──────────────────────────────
        relevant_hits = await asyncio.to_thread(
            self._filter_relevant_hits, fused_hits, judge_result
        )
        sources_payload = self._build_sources_payload(relevant_hits)

        # ── Step 6: Answer Synthesis ──────────────────────────────────
        if self.stage_callback:
            await self.stage_callback("generating")

        context_str = await asyncio.to_thread(
            self._format_context_for_llm, relevant_hits
        )

        context_summary_parts = []
        for k, v in context.items():
            if isinstance(v, list):
                context_summary_parts.append(f"{k}: {', '.join(str(x) for x in v)}")
            else:
                context_summary_parts.append(f"{k}: {v}")
        context_summary = " | ".join(context_summary_parts) if context_summary_parts else ""

        rag_user_message = (
            f"Felhasználó kérdése: {user_query}\n\n"
            f"{'Összegyűjtött kontextus: ' + context_summary + chr(10) if context_summary else ''}"
            f"AI által validált, releváns találatok:\n{context_str}"
        )

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        # Include last 10 turns of conversation history
        messages += history[-20:]
        messages.append({"role": "user", "content": rag_user_message})

        answer = await asyncio.to_thread(
            call_llm_sync,
            messages,
            settings.MAX_TOKENS,
            settings.TEMPERATURE,
        )

        if self.stage_callback:
            await self.stage_callback("done")

        return {
            "answer": answer,
            "sources": sources_payload,
            "needs_clarification": False,
            "clarification_text": None,
            "search_queries": search_queries,
            "entities": extracted,
        }
