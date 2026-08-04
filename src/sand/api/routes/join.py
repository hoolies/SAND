"""Explicit join, suggestions, estimates, and saved recipes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, model_validator

from sand.api.errors import dataset_client, error_detail, http_error_from_exc, open_dataset, tabular_result
from sand.core.dataset_meta import delete_recipe, get_recipe, list_recipes, save_recipe
from sand.core.store import DatasetStore
from sand.queries.join_suggest import estimate_join, suggest_join_keys
from sand.queries.joins import JoinPlan, JoinSpec, execute_join, execute_join_plan

router = APIRouter()


class JoinRequest(BaseModel):
    dataset_id: str
    join: JoinSpec | None = None
    plan: JoinPlan | None = None
    recipe_name: str | None = None


class SuggestRequest(BaseModel):
    dataset_id: str
    left: str
    right: str


class EstimateRequest(BaseModel):
    dataset_id: str
    join: JoinSpec


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


@router.post("/join")
def run_join(body: JoinRequest) -> dict:
    store = DatasetStore()
    client = open_dataset(body.dataset_id, store=store)

    try:
        if body.recipe_name and body.join is None and body.plan is None:
            recipe = get_recipe(client, body.recipe_name)
            if recipe is None:
                raise ValueError(f"Unknown recipe: {body.recipe_name}")
            if recipe.plan is not None:
                body.plan = recipe.plan
            else:
                body.join = recipe.spec

        if body.join is None and body.plan is None:
            raise ValueError("Provide join, plan, or recipe_name")

        if body.plan is not None:
            df, sql = execute_join_plan(client, body.plan)
            spec_dump = body.plan.model_dump()
            as_table = body.plan.as_table
            estimate = None
            if body.recipe_name:
                save_recipe(client, body.recipe_name, plan=body.plan)
        else:
            assert body.join is not None
            estimate = estimate_join(client, body.join)
            df, sql = execute_join(client, body.join)
            spec_dump = body.join.model_dump()
            as_table = body.join.as_table
            if body.recipe_name:
                save_recipe(client, body.recipe_name, spec=body.join)

        payload = tabular_result(
            dataset_id=body.dataset_id,
            df=df,
            action="join",
            sql=sql,
            as_table=as_table,
            estimate=estimate.model_dump() if estimate is not None else None,
            spec=spec_dump,
        )
        return payload
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
            estimate = estimate_join(client, body.join)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc
    return {"dataset_id": body.dataset_id, "estimate": estimate.model_dump()}


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
        client = open_dataset(body.dataset_id)
        recipe = save_recipe(client, body.name, spec=body.join, plan=body.plan)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc
    return {"dataset_id": body.dataset_id, "recipe": recipe.model_dump()}


@router.delete("/join/recipes/{dataset_id}/{name}")
def recipes_delete(dataset_id: str, name: str) -> dict:
    try:
        client = open_dataset(dataset_id)
        ok = delete_recipe(client, name)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise http_error_from_exc(exc) from exc
    if not ok:
        raise HTTPException(status_code=404, detail=error_detail("not_found", "Recipe not found"))
    return {"deleted": True}
