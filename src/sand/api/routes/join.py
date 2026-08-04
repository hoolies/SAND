"""Explicit join, suggestions, estimates, and saved recipes."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, model_validator

from sand.api.errors import dataset_client, error_detail, http_error_from_exc, tabular_result
from sand.core.dataset_meta import delete_recipe, get_recipe, list_recipes, save_recipe
from sand.core.store import DatasetStore
from sand.queries.join_suggest import estimate_join, estimate_join_plan, suggest_join_keys
from sand.queries.joins import JoinPlan, JoinSpec, execute_join, execute_join_plan

router = APIRouter()


class JoinRequest(BaseModel):
    dataset_id: str
    join: JoinSpec | None = None
    plan: JoinPlan | None = None
    recipe_name: str | None = None
    write: bool | None = None  # None = infer from as_table / recipe save


class SuggestRequest(BaseModel):
    dataset_id: str
    left: str
    right: str


class EstimateRequest(BaseModel):
    dataset_id: str
    join: JoinSpec | None = None
    plan: JoinPlan | None = None

    @model_validator(mode="after")
    def _one_of(self) -> EstimateRequest:
        if self.join is None and self.plan is None:
            raise ValueError("Provide join or plan")
        if self.join is not None and self.plan is not None:
            raise ValueError("Provide only one of join or plan")
        return self


class RecipeSaveRequest(BaseModel):
    dataset_id: str
    name: str
    join: JoinSpec | None = None
    plan: JoinPlan | None = None

    @model_validator(mode="after")
    def _one_of(self) -> RecipeSaveRequest:
        if self.join is None and self.plan is None:
            raise ValueError("Provide join or plan")
        if self.join is not None and self.plan is not None:
            raise ValueError("Provide only one of join or plan")
        return self


def _as_table_of(body: JoinRequest) -> str | None:
    if body.plan is not None:
        return body.plan.as_table or (body.plan.steps[-1].as_table if body.plan.steps else None)
    if body.join is not None:
        return body.join.as_table
    return None


@router.post("/join")
def run_join(
    body: JoinRequest,
    x_sand_query_id: str | None = Header(default=None, alias="X-SAND-Query-Id"),
) -> dict:
    store = DatasetStore()
    # Save-on-run only when the client sent a join/plan with a recipe name.
    persist_recipe = bool(body.recipe_name) and (body.join is not None or body.plan is not None)

    try:
        if body.recipe_name and body.join is None and body.plan is None:
            with dataset_client(body.dataset_id, store=store, read_only=True) as peek:
                recipe = get_recipe(peek, body.recipe_name)
                if recipe is None:
                    raise ValueError(f"Unknown recipe: {body.recipe_name}")
                if recipe.plan is not None:
                    body.plan = recipe.plan
                else:
                    body.join = recipe.spec

        if body.join is None and body.plan is None:
            raise ValueError("Provide join, plan, or recipe_name")

        inferred_write = persist_recipe or bool(_as_table_of(body))
        if body.write is False and inferred_write:
            raise ValueError("write=false but as_table / recipe save requires a write connection")
        needs_write = body.write if body.write is not None else inferred_write

        with dataset_client(
            body.dataset_id,
            store=store,
            read_only=not needs_write,
            track=True,
            query_id=x_sand_query_id,
        ) as client:
            if body.plan is not None:
                df, sql = execute_join_plan(client, body.plan)
                spec_dump = body.plan.model_dump()
                as_table = body.plan.as_table
                estimate = None
                if persist_recipe and body.recipe_name:
                    save_recipe(client, body.recipe_name, plan=body.plan)
            else:
                assert body.join is not None
                estimate = estimate_join(client, body.join)
                df, sql = execute_join(client, body.join)
                spec_dump = body.join.model_dump()
                as_table = body.join.as_table
                if persist_recipe and body.recipe_name:
                    save_recipe(client, body.recipe_name, spec=body.join)

            return tabular_result(
                dataset_id=body.dataset_id,
                df=df,
                action="join",
                sql=sql,
                as_table=as_table,
                estimate=estimate.model_dump() if estimate is not None else None,
                spec=spec_dump,
            )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc


@router.post("/join/suggest")
def join_suggest(body: SuggestRequest) -> dict:
    try:
        with dataset_client(body.dataset_id, read_only=True) as client:
            suggestions = suggest_join_keys(client, body.left, body.right)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc
    return {"dataset_id": body.dataset_id, "suggestions": [s.model_dump() for s in suggestions]}


@router.post("/join/estimate")
def join_estimate(body: EstimateRequest) -> dict:
    try:
        with dataset_client(body.dataset_id, read_only=True) as client:
            if body.plan is not None:
                plan_estimate = estimate_join_plan(client, body.plan)
                return {
                    "dataset_id": body.dataset_id,
                    "estimate": plan_estimate.model_dump(),
                    "plan": True,
                }
            assert body.join is not None
            join_estimate_result = estimate_join(client, body.join)
            return {
                "dataset_id": body.dataset_id,
                "estimate": join_estimate_result.model_dump(),
                "plan": False,
            }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc


@router.get("/join/recipes/{dataset_id}")
def recipes_list(dataset_id: str) -> dict:
    try:
        with dataset_client(dataset_id, read_only=True) as client:
            recipes = list_recipes(client)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc
    return {"dataset_id": dataset_id, "recipes": [r.model_dump() for r in recipes]}


@router.post("/join/recipes")
def recipes_save(body: RecipeSaveRequest) -> dict:
    try:
        with dataset_client(body.dataset_id, read_only=False) as client:
            recipe = save_recipe(client, body.name, spec=body.join, plan=body.plan)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc
    return {"dataset_id": body.dataset_id, "recipe": recipe.model_dump()}


@router.delete("/join/recipes/{dataset_id}/{name}")
def recipes_delete(dataset_id: str, name: str) -> dict:
    try:
        with dataset_client(dataset_id, read_only=False) as client:
            ok = delete_recipe(client, name)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc
    if not ok:
        raise HTTPException(status_code=404, detail=error_detail("not_found", "Recipe not found"))
    return {"deleted": True}
