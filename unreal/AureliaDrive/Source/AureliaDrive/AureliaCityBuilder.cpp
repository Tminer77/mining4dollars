#include "AureliaCityBuilder.h"

#include "AureliaWorld.h"
#include "Components/SceneComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/CollisionProfile.h"
#include "Engine/StaticMesh.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "UObject/ConstructorHelpers.h"

AAureliaCityBuilder::AAureliaCityBuilder()
{
	PrimaryActorTick.bCanEverTick = false;
	USceneComponent* Root = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
	SetRootComponent(Root);

	static ConstructorHelpers::FObjectFinder<UStaticMesh> Cube(TEXT("/Engine/BasicShapes/Cube.Cube"));
	static ConstructorHelpers::FObjectFinder<UStaticMesh> Cylinder(TEXT("/Engine/BasicShapes/Cylinder.Cylinder"));
	static ConstructorHelpers::FObjectFinder<UMaterialInterface> Shape(TEXT("/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial"));
	if (Cube.Succeeded())
	{
		CubeMesh = Cube.Object;
	}
	if (Cylinder.Succeeded())
	{
		CylinderMesh = Cylinder.Object;
	}
	if (Shape.Succeeded())
	{
		ShapeMaterial = Shape.Object;
	}

	Rng.Initialize(20260831);
}

void AAureliaCityBuilder::Build()
{
	using namespace AureliaWorld;

	const float City = Grid * Cell;
	const float Origin = -City * 0.5f + Cell * 0.5f;

	AddBox(FVector(0.f, 0.f, -30.f), FVector(City + 40000.f, City + 40000.f, 40.f) / 100.f, FLinearColor(0.72f, 0.55f, 0.38f), true);
	AddBox(FVector(0.f, 0.f, 2.f), FVector(City + Road, City + Road, 8.f) / 100.f, FLinearColor(0.07f, 0.08f, 0.09f), true);

	static const FLinearColor Paint[] = {
		FLinearColor(0.12f, 0.09f, 0.14f),
		FLinearColor(0.18f, 0.11f, 0.10f),
		FLinearColor(0.10f, 0.14f, 0.18f),
		FLinearColor(0.22f, 0.16f, 0.12f),
		FLinearColor(0.08f, 0.12f, 0.14f),
		FLinearColor(0.16f, 0.10f, 0.12f),
	};
	static const FLinearColor Neon[] = {
		FLinearColor(1.f, 0.28f, 0.62f),
		FLinearColor(0.30f, 0.92f, 1.f),
		FLinearColor(1.f, 0.68f, 0.28f),
		FLinearColor(0.45f, 1.f, 0.70f),
	};

	for (int32 Gx = 0; Gx < Grid; ++Gx)
	{
		for (int32 Gz = 0; Gz < Grid; ++Gz)
		{
			const float Cx = Origin + Gx * Cell;
			const float Cy = Origin + Gz * Cell;
			const bool bPlaza = (Gx == 2 && Gz == 2) || (Gx == 1 && Gz == 4) || (Gx == 4 && Gz == 1);

			AddBox(FVector(Cx, Cy, 18.f), FVector(Block - 200.f, Block - 200.f, 36.f) / 100.f, FLinearColor(0.38f, 0.32f, 0.28f), true);

			if (bPlaza)
			{
				for (int32 P = 0; P < 5; ++P)
				{
					AddPalm(FVector(Cx + (P - 2) * 600.f, Cy + ((P % 2) * 2 - 1) * 800.f, 0.f));
				}
				continue;
			}

			const int32 Count = 2 + ((Gx + Gz) % 2);
			for (int32 I = 0; I < Count; ++I)
			{
				const float W = 1000.f + static_cast<float>((Gx * 3 + I * 5) % 12) * 100.f;
				const float D = 1000.f + static_cast<float>((Gz * 5 + I * 7) % 12) * 100.f;
				const float H = 1200.f + static_cast<float>((Gx * 11 + Gz * 7 + I * 13) % 28) * 100.f + Rng.FRandRange(0.f, 400.f);
				const float Ox = ((I % 2) * 2 - 1) * (Block - 400.f) * 0.25f;
				const float Oy = (I < 2 ? -1.f : 1.f) * (Block - 400.f) * 0.22f;
				AddBox(FVector(Cx + Ox, Cy + Oy, H * 0.5f), FVector(W, D, H) / 100.f, Paint[(Gx + Gz + I) % 6], true);
				if (H > 2200.f && I == 0)
				{
					AddBox(FVector(Cx + Ox, Cy + Oy + D * 0.5f + 20.f, H + 80.f), FVector(6.5f, 0.18f, 1.1f), Neon[(Gx + Gz) % 4], false);
				}
			}
		}
	}

	AddBox(FVector(0.f, City * 0.5f + 2200.f, 20.f), FVector(City + 8000.f, 400.f, 160.f) / 100.f, FLinearColor(0.42f, 0.36f, 0.30f), true);
	AddBox(FVector(0.f, City * 0.5f + 90000.f, -40.f), FVector(1800.f, 1800.f, 0.6f), FLinearColor(0.02f, 0.16f, 0.22f), false);

	for (int32 I = -10; I <= 10; ++I)
	{
		AddPalm(FVector(I * 1800.f, City * 0.5f + 1200.f, 0.f));
	}
}

void AAureliaCityBuilder::AddBox(const FVector& Location, const FVector& Scale, const FLinearColor& Color, bool bCollision)
{
	if (!CubeMesh)
	{
		return;
	}

	UStaticMeshComponent* Comp = NewObject<UStaticMeshComponent>(this);
	Comp->SetStaticMesh(CubeMesh);
	Comp->SetMaterial(0, Paint(Color));
	Comp->SetCollisionEnabled(bCollision ? ECollisionEnabled::QueryAndPhysics : ECollisionEnabled::NoCollision);
	if (bCollision)
	{
		Comp->SetCollisionProfileName(UCollisionProfile::BlockAll_ProfileName);
	}
	Comp->SetupAttachment(GetRootComponent());
	Comp->SetRelativeLocation(Location);
	Comp->SetRelativeScale3D(Scale);
	Comp->RegisterComponent();
}

void AAureliaCityBuilder::AddPalm(const FVector& Location)
{
	if (!CylinderMesh || !CubeMesh)
	{
		return;
	}

	UStaticMeshComponent* Trunk = NewObject<UStaticMeshComponent>(this);
	Trunk->SetStaticMesh(CylinderMesh);
	Trunk->SetMaterial(0, Paint(FLinearColor(0.32f, 0.20f, 0.12f)));
	Trunk->SetupAttachment(GetRootComponent());
	Trunk->SetRelativeLocation(Location + FVector(0.f, 0.f, 360.f));
	Trunk->SetRelativeScale3D(FVector(0.28f, 0.28f, 7.2f));
	Trunk->SetCollisionEnabled(ECollisionEnabled::NoCollision);
	Trunk->RegisterComponent();

	for (int32 I = 0; I < 6; ++I)
	{
		UStaticMeshComponent* Frond = NewObject<UStaticMeshComponent>(this);
		Frond->SetStaticMesh(CubeMesh);
		Frond->SetMaterial(0, Paint(FLinearColor(0.08f, 0.32f, 0.12f)));
		Frond->SetupAttachment(GetRootComponent());
		const float Yaw = I * 60.f;
		Frond->SetRelativeLocation(Location + FVector(0.f, 0.f, 740.f));
		Frond->SetRelativeRotation(FRotator(25.f, Yaw, 0.f));
		Frond->SetRelativeScale3D(FVector(3.4f, 0.18f, 0.08f));
		Frond->SetCollisionEnabled(ECollisionEnabled::NoCollision);
		Frond->RegisterComponent();
	}
}

UMaterialInstanceDynamic* AAureliaCityBuilder::Paint(const FLinearColor& Color)
{
	if (!ShapeMaterial)
	{
		return nullptr;
	}

	UMaterialInstanceDynamic* Mid = UMaterialInstanceDynamic::Create(ShapeMaterial, this);
	Mid->SetVectorParameterValue(TEXT("Color"), Color);
	Mid->SetVectorParameterValue(TEXT("BaseColor"), Color);
	Paints.Add(Mid);
	return Mid;
}
